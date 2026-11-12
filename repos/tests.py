import base64
import hashlib
import hmac
import json
from unittest import mock
from urllib.parse import parse_qs, unquote, urlparse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from kwizzes.models import Kwiz

from . import github
from .models import Installation, LinkedFile, Repo

User = get_user_model()

KWIZ_MD = """# Sorting

## Which sort is stable?
- [ ] Quicksort
- [x] Merge sort
"""

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
APP = {
    "SLUG": "kahduke-test",
    "CLIENT_ID": "Iv1.test",
    "CLIENT_SECRET": "shh",
    "PRIVATE_KEY": PRIVATE_PEM,
    "WEBHOOK_SECRET": "hook-secret",
}
NO_APP = dict.fromkeys(APP, "")


def gh_error(status, detail=""):
    return github.GitHubError(github._explain(status, detail), status=status)


class FakeGitHub:
    """GitHub, as far as KahDuke uses it, behind the client's `_call`.

    One account, `me`, owns two repos: `me/kwizzes` (private) and
    `me/public` (public). The app is installed on `me` (installation 42)
    and covers `me/kwizzes` only. The GitHub user behind OAuth code
    "good-code" can see that installation.
    """

    def __init__(self):
        self.repos = {
            "me/kwizzes": {
                "private": True,
                "files": {
                    "kwizzes/sorting.md": KWIZ_MD,
                    "kwizzes/README.md": "# hi",
                    "other/x.md": KWIZ_MD,
                },
            },
            "me/public": {"private": False, "files": {"k.md": KWIZ_MD}},
        }
        self.installation_repos = ["me/kwizzes"]
        self.tokens = {}  # token -> (repo names or None, access)
        self.minted = []
        self.commits = []

    @staticmethod
    def sha(text):
        return hashlib.sha1(text.encode()).hexdigest()

    def _mint(self, token, body):
        claims = jwt.decode(token, _KEY.public_key(), algorithms=["RS256"])
        assert claims["iss"] == APP["CLIENT_ID"]
        wanted = body.get("repositories")
        covered = [n.split("/")[1] for n in self.installation_repos]
        if wanted and not set(wanted) <= set(covered):
            raise gh_error(422, "repositories not in installation")
        access = body["permissions"]["contents"]
        new = f"ghs_{len(self.minted)}"
        self.tokens[new] = (wanted, access)
        self.minted.append((wanted, access))
        return {"token": new, "expires_at": "2099-01-01T00:00:00Z"}

    def _allowed(self, token, repo_name, write):
        repo = self.repos[repo_name]
        if token in self.tokens:
            names, access = self.tokens[token]
            in_scope = (
                f"me/{names[0]}" == repo_name
                if names
                else (repo_name in self.installation_repos)
            )
            if in_scope and (access == "write" or not write):
                return True
        return not repo["private"] and not write

    def __call__(self, method, url, token="", body=None, auth="Bearer"):
        parsed = urlparse(url)
        path = unquote(parsed.path)
        if parsed.netloc == "github.com" and path == "/login/oauth/access_token":
            if body["code"] == "good-code" and body["client_secret"] == "shh":
                return {"access_token": "gho_user"}
            return {"error": "bad_verification_code", "error_description": "Bad code."}
        if path.startswith("/app/installations/"):
            if path != "/app/installations/42/access_tokens":
                raise gh_error(404)
            return self._mint(token, body)
        if path == "/installation/repositories":
            names = [{"full_name": n} for n in self.installation_repos]
            return {"total_count": len(names), "repositories": names}
        if path == "/user/installations":
            if token != "gho_user":
                raise gh_error(401)
            install = {"id": 42, "account": {"login": "me", "type": "User"}}
            return {"total_count": 1, "installations": [install]}

        parts = path.removeprefix("/repos/").split("/", 2)
        repo_name = "/".join(parts[:2])
        rest = parts[2] if len(parts) > 2 else ""
        if repo_name not in self.repos:
            raise gh_error(404)
        repo = self.repos[repo_name]
        if not self._allowed(token, repo_name, write=method == "PUT"):
            raise gh_error(403 if method == "PUT" and token else 404)
        if method == "GET" and not rest:
            return {"full_name": repo_name, "default_branch": "main"}
        if method == "GET" and rest.startswith("git/trees/"):
            return {
                "tree": [
                    {"path": p, "type": "blob", "sha": self.sha(t)}
                    for p, t in repo["files"].items()
                ]
            }
        file_path = rest.removeprefix("contents/")
        if method == "GET":
            if file_path not in repo["files"]:
                raise gh_error(404)
            text = repo["files"][file_path]
            return {
                "type": "file",
                "sha": self.sha(text),
                "content": base64.b64encode(text.encode()).decode(),
            }
        if method == "PUT":
            current = repo["files"].get(file_path)
            if current is not None and body.get("sha") != self.sha(current):
                raise gh_error(409, "conflict")
            text = base64.b64decode(body["content"]).decode()
            repo["files"][file_path] = text
            self.commits.append((repo_name, file_path, body["message"]))
            return {"content": {"sha": self.sha(text)}}
        raise AssertionError(f"unexpected {method} {url}")


@override_settings(GITHUB_APP=APP)
class GitHubTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="ada", email="ada@example.com", password="pw"
        )
        self.client.force_login(self.user)
        self.gh = FakeGitHub()
        patcher = mock.patch.object(github, "_call", self.gh)
        patcher.start()
        self.addCleanup(patcher.stop)

    def connect_app(self):
        """Run the sign-in-with-GitHub round trip."""
        response = self.client.get(reverse("repos:github_connect"))
        state = parse_qs(urlparse(response["Location"]).query)["state"][0]
        return self.client.get(
            reverse("repos:github_callback"), {"code": "good-code", "state": state}
        )

    def link_repo(self, url="https://github.com/me/kwizzes/tree/main/kwizzes", **extra):
        response = self.client.post(reverse("repos:list"), {"full_name": url} | extra)
        return response, Repo.objects.filter(owner=self.user).last()

    def import_sorting(self, **extra):
        self.connect_app()
        _, repo = self.link_repo(**extra)
        self.client.post(
            reverse("repos:import", args=[repo.pk]), {"file_path": "kwizzes/sorting.md"}
        )
        return LinkedFile.objects.get()


class AppConnectionTests(GitHubTestCase):
    def test_install_sends_the_user_to_github_with_state(self):
        response = self.client.get(reverse("repos:github_install"))
        self.assertTrue(
            response["Location"].startswith(
                "https://github.com/apps/kahduke-test/installations/new?state="
            )
        )

    def test_callback_records_installations_the_user_can_see(self):
        response = self.connect_app()
        self.assertRedirects(
            response, reverse("repos:list"), fetch_redirect_response=False
        )
        installation = Installation.objects.get()
        self.assertEqual(
            (installation.installation_id, installation.account_login), (42, "me")
        )
        self.assertEqual(list(installation.users.all()), [self.user])
        listing = self.client.get(reverse("repos:list"))
        self.assertContains(listing, "me/kwizzes")

    def test_callback_rejects_a_forged_state(self):
        self.client.get(reverse("repos:github_connect"))
        self.client.get(
            reverse("repos:github_callback"), {"code": "good-code", "state": "forged"}
        )
        self.assertFalse(Installation.objects.exists())

    def test_install_from_github_without_state_signs_in_again(self):
        response = self.client.get(
            reverse("repos:github_callback"),
            {"code": "good-code", "installation_id": "42", "setup_action": "install"},
        )
        self.assertRedirects(
            response, reverse("repos:github_connect"), fetch_redirect_response=False
        )
        self.assertFalse(Installation.objects.exists())

    def test_bad_code(self):
        response = self.client.get(reverse("repos:github_connect"))
        state = parse_qs(urlparse(response["Location"]).query)["state"][0]
        response = self.client.get(
            reverse("repos:github_callback"),
            {"code": "nope", "state": state},
            follow=True,
        )
        self.assertContains(response, "Bad code.")

    @override_settings(GITHUB_APP=NO_APP)
    def test_app_pages_are_off_without_configuration(self):
        self.assertEqual(
            self.client.get(reverse("repos:github_install")).status_code, 404
        )
        self.assertNotContains(self.client.get(reverse("repos:list")), "Install on")


class LinkingTests(GitHubTestCase):
    def test_public_repo_without_the_app(self):
        _, repo = self.link_repo("me/public")
        self.assertIsNone(repo.installation)
        self.assertEqual(repo.branch, "main")
        self.assertEqual(self.gh.minted, [])

    def test_private_repo_needs_the_app(self):
        response, repo = self.link_repo()
        self.assertIsNone(repo)
        self.assertContains(response, "install the KahDuke GitHub App on it")

    def test_committing_needs_the_app(self):
        response, repo = self.link_repo("me/public", can_write="on")
        self.assertIsNone(repo)
        self.assertContains(response, "Committing needs the KahDuke GitHub App")

    def test_private_repo_through_the_app(self):
        self.connect_app()
        _, repo = self.link_repo()
        self.assertEqual(repo.installation.installation_id, 42)
        self.assertEqual(
            (repo.full_name, repo.branch, repo.path), ("me/kwizzes", "main", "kwizzes")
        )
        # Read-only and limited to this one repo.
        self.assertEqual(self.gh.minted, [(["kwizzes"], "read")])

    def test_repo_outside_the_installation(self):
        self.connect_app()
        self.gh.repos["me/secret"] = {"private": True, "files": {}}
        response, repo = self.link_repo("me/secret")
        self.assertIsNone(repo)
        self.assertContains(response, "doesn&#x27;t include me/secret")

    def test_lists_only_md_files_in_the_folder(self):
        self.connect_app()
        _, repo = self.link_repo()
        response = self.client.get(reverse("repos:detail", args=[repo.pk]))
        self.assertContains(response, "sorting.md")
        self.assertNotContains(response, "x.md")
        self.assertNotContains(response, "README")

    def test_tokens_are_cached(self):
        self.connect_app()
        _, repo = self.link_repo()
        for _ in range(3):
            self.client.get(reverse("repos:detail", args=[repo.pk]))
        self.assertEqual(len(self.gh.minted), 1)

    def test_other_users_cannot_see_the_repo_or_use_the_installation(self):
        self.connect_app()
        _, repo = self.link_repo()
        other = User.objects.create_user(
            username="bob", email="bob@example.com", password="pw"
        )
        self.client.force_login(other)
        self.assertEqual(
            self.client.get(reverse("repos:detail", args=[repo.pk])).status_code, 404
        )
        self.link_repo()
        self.assertFalse(Repo.objects.filter(owner=other).exists())


class SyncTests(GitHubTestCase):
    def test_import_then_pull(self):
        link = self.import_sorting()
        kwiz = link.kwiz
        self.assertEqual(kwiz.owner, self.user)
        choice_ids = list(
            kwiz.ordered_slots().first().kwestion.choices.values_list("pk", flat=True)
        )
        files = self.gh.repos["me/kwizzes"]["files"]
        files["kwizzes/sorting.md"] = KWIZ_MD.replace("Merge sort", "Mergesort")
        listing = self.client.get(reverse("repos:detail", args=[link.repo.pk]))
        self.assertContains(listing, "changed on GitHub")
        self.client.post(reverse("repos:pull", args=[link.pk]))
        kwestion = kwiz.ordered_slots().first().kwestion
        self.assertEqual(kwestion.choices.get(position=1).body, "Mergesort")
        self.assertEqual(
            list(kwestion.choices.values_list("pk", flat=True)), choice_ids
        )

    def test_import_outside_the_folder_is_refused(self):
        self.connect_app()
        _, repo = self.link_repo()
        self.client.post(
            reverse("repos:import", args=[repo.pk]), {"file_path": "other/x.md"}
        )
        self.assertFalse(Kwiz.objects.filter(owner=self.user).exists())

    def test_read_only_link_cannot_commit(self):
        link = self.import_sorting()
        self.client.post(reverse("repos:push", args=[link.pk]))
        self.assertEqual(self.gh.commits, [])
        self.assertNotIn((["kwizzes"], "write"), self.gh.minted)

    def test_commit_uses_a_write_token_and_detects_conflicts(self):
        link = self.import_sorting(can_write="on")
        kwiz = link.kwiz
        kwiz.title = "Sorting, again"
        kwiz.save()
        self.client.post(reverse("repos:push", args=[link.pk]))
        files = self.gh.repos["me/kwizzes"]["files"]
        self.assertIn("# Sorting, again", files["kwizzes/sorting.md"])
        self.assertIn((["kwizzes"], "write"), self.gh.minted)

        files["kwizzes/sorting.md"] += "\n## Someone else's kwestion\n- [x] yes\n"
        response = self.client.post(reverse("repos:push", args=[link.pk]), follow=True)
        self.assertContains(response, "changed on GitHub")
        self.assertEqual(len(self.gh.commits), 1)

    def test_commit_a_new_kwiz(self):
        self.connect_app()
        _, repo = self.link_repo(can_write="on")
        kwiz = Kwiz.objects.create(owner=self.user, title="Graphs & Trees")
        self.client.post(reverse("repos:push_new", args=[repo.pk]), {"kwiz": kwiz.pk})
        self.assertIn("kwizzes/graphs-trees.md", self.gh.repos["me/kwizzes"]["files"])
        self.assertEqual(kwiz.repo_file.file_path, "kwizzes/graphs-trees.md")

    def test_failed_commit_leaves_no_link(self):
        self.connect_app()
        _, repo = self.link_repo(can_write="on")
        self.gh.repos["me/kwizzes"]["files"]["kwizzes/graphs.md"] = "taken"
        kwiz = Kwiz.objects.create(owner=self.user, title="Graphs")
        self.client.post(reverse("repos:push_new", args=[repo.pk]), {"kwiz": kwiz.pk})
        self.assertFalse(LinkedFile.objects.exists())


class WebhookTests(GitHubTestCase):
    def deliver(self, event, payload, secret=APP["WEBHOOK_SECRET"]):
        body = json.dumps(payload).encode()
        signature = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
        return self.client.post(
            reverse("repos:github_webhook"),
            body,
            content_type="application/json",
            headers={"X-GitHub-Event": event, "X-Hub-Signature-256": signature},
        )

    def push_payload(self, *modified, branch="main"):
        return {
            "ref": f"refs/heads/{branch}",
            "repository": {"full_name": "me/kwizzes"},
            "installation": {"id": 42},
            "commits": [{"added": [], "modified": list(modified)}],
        }

    def test_bad_signature_is_rejected(self):
        response = self.deliver("push", self.push_payload(), secret="wrong")
        self.assertEqual(response.status_code, 401)

    def test_push_pulls_changed_kwizzes(self):
        link = self.import_sorting()
        files = self.gh.repos["me/kwizzes"]["files"]
        files["kwizzes/sorting.md"] = KWIZ_MD.replace("# Sorting", "# Sorted")
        self.client.logout()  # webhooks arrive with no session
        response = self.deliver("push", self.push_payload("kwizzes/sorting.md"))
        self.assertContains(response, "updated 1")
        link.kwiz.refresh_from_db()
        self.assertEqual(link.kwiz.title, "Sorted")

    def test_push_to_another_branch_or_file_is_ignored(self):
        link = self.import_sorting()
        files = self.gh.repos["me/kwizzes"]["files"]
        files["kwizzes/sorting.md"] = KWIZ_MD.replace("# Sorting", "# Sorted")
        self.assertContains(
            self.deliver("push", self.push_payload("kwizzes/sorting.md", branch="dev")),
            "updated 0",
        )
        self.assertContains(
            self.deliver("push", self.push_payload("other/x.md")), "updated 0"
        )
        link.kwiz.refresh_from_db()
        self.assertEqual(link.kwiz.title, "Sorting")

    def test_broken_markdown_keeps_the_last_good_kwiz(self):
        link = self.import_sorting()
        self.gh.repos["me/kwizzes"]["files"]["kwizzes/sorting.md"] = "## no title\n"
        with self.assertLogs("repos.sync", "WARNING"):
            response = self.deliver("push", self.push_payload("kwizzes/sorting.md"))
        self.assertContains(response, "updated 0")
        link.kwiz.refresh_from_db()
        self.assertEqual(link.kwiz.title, "Sorting")

    def test_uninstall_forgets_the_installation(self):
        link = self.import_sorting()
        self.deliver("installation", {"action": "deleted", "installation": {"id": 42}})
        self.assertFalse(Installation.objects.exists())
        link.repo.refresh_from_db()
        self.assertIsNone(link.repo.installation)
        self.assertTrue(Kwiz.objects.filter(pk=link.kwiz.pk).exists())

    @override_settings(GITHUB_APP=APP | {"WEBHOOK_SECRET": ""})
    def test_webhook_is_off_without_a_secret(self):
        self.assertEqual(self.deliver("push", self.push_payload()).status_code, 404)
