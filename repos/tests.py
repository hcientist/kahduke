import base64
import hashlib
from unittest import mock
from urllib.parse import unquote, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from kwizzes.models import Kwiz

from . import github
from .models import LinkedFile, Repo

User = get_user_model()

KWIZ_MD = """# Sorting

## Which sort is stable?
- [ ] Quicksort
- [x] Merge sort
"""


class FakeGitHub:
    """An in-memory repo behind the same `_call` the real client uses."""

    def __init__(self, files, private=False, token="tok", writable=True):
        self.files = dict(files)
        self.private = private
        self.token = token
        self.writable = writable
        self.commits = []

    @staticmethod
    def sha(text):
        return hashlib.sha1(text.encode()).hexdigest()

    def __call__(self, method, url, token="", body=None):
        if self.private and token != self.token:
            raise github.GitHubError(github._explain(404, "not found"), status=404)
        path = unquote(urlparse(url).path).removeprefix("/repos/me/kwizzes")
        if method == "GET" and path == "":
            return {"full_name": "me/kwizzes", "default_branch": "main"}
        if method == "GET" and path.startswith("/git/trees/"):
            return {
                "tree": [
                    {"path": p, "type": "blob", "sha": self.sha(t)}
                    for p, t in self.files.items()
                ]
            }
        file_path = path.removeprefix("/contents/")
        if method == "GET":
            if file_path not in self.files:
                raise github.GitHubError(github._explain(404, "not found"), status=404)
            text = self.files[file_path]
            return {
                "type": "file",
                "sha": self.sha(text),
                "content": base64.b64encode(text.encode()).decode(),
            }
        if method == "PUT":
            if not self.writable or token != self.token:
                raise github.GitHubError(github._explain(403, "forbidden"), status=403)
            current = self.files.get(file_path)
            if current is not None and body.get("sha") != self.sha(current):
                raise github.GitHubError(github._explain(409, "conflict"), status=409)
            text = base64.b64decode(body["content"]).decode()
            self.files[file_path] = text
            self.commits.append((file_path, body["message"]))
            return {"content": {"sha": self.sha(text)}}
        raise AssertionError(f"unexpected {method} {url}")


class RepoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ada", email="ada@example.com", password="pw"
        )
        self.client.force_login(self.user)
        self.gh = FakeGitHub(
            {"kwizzes/sorting.md": KWIZ_MD, "README.md": "# hi", "other/x.md": KWIZ_MD},
            private=True,
        )
        patcher = mock.patch.object(github, "_call", self.gh)
        patcher.start()
        self.addCleanup(patcher.stop)

    def link_repo(self, **extra):
        data = {
            "full_name": "https://github.com/me/kwizzes/tree/main/kwizzes",
            "token": "tok",
        }
        response = self.client.post(reverse("repos:list"), data | extra)
        return response, Repo.objects.filter(owner=self.user).first()

    def test_link_from_a_folder_url(self):
        response, repo = self.link_repo()
        self.assertRedirects(response, reverse("repos:detail", args=[repo.pk]))
        self.assertEqual(
            (repo.full_name, repo.branch, repo.path), ("me/kwizzes", "main", "kwizzes")
        )

    def test_private_repo_needs_the_token(self):
        response, repo = self.link_repo(token="")
        self.assertIsNone(repo)
        self.assertContains(response, "doesn&#x27;t exist")

    def test_write_needs_a_token(self):
        self.gh.private = False
        response, repo = self.link_repo(token="", can_write="on")
        self.assertIsNone(repo)
        self.assertContains(response, "Writing to a repo needs a token.")

    def test_lists_only_md_files_in_the_folder(self):
        _, repo = self.link_repo()
        response = self.client.get(reverse("repos:detail", args=[repo.pk]))
        self.assertContains(response, "sorting.md")
        self.assertNotContains(response, "x.md")
        self.assertNotContains(response, "README")

    def test_import_then_pull(self):
        _, repo = self.link_repo()
        self.client.post(
            reverse("repos:import", args=[repo.pk]), {"file_path": "kwizzes/sorting.md"}
        )
        kwiz = Kwiz.objects.get(owner=self.user)
        link = kwiz.repo_file
        self.assertEqual(link.file_path, "kwizzes/sorting.md")
        choice_ids = list(
            kwiz.ordered_slots().first().kwestion.choices.values_list("pk", flat=True)
        )

        self.gh.files["kwizzes/sorting.md"] = KWIZ_MD.replace("Merge sort", "Mergesort")
        listing = self.client.get(reverse("repos:detail", args=[repo.pk]))
        self.assertContains(listing, "changed on GitHub")
        self.client.post(reverse("repos:pull", args=[link.pk]))
        kwestion = kwiz.ordered_slots().first().kwestion
        self.assertEqual(kwestion.choices.get(position=1).body, "Mergesort")
        self.assertEqual(
            list(kwestion.choices.values_list("pk", flat=True)), choice_ids
        )

    def test_import_outside_the_folder_is_refused(self):
        _, repo = self.link_repo()
        self.client.post(
            reverse("repos:import", args=[repo.pk]), {"file_path": "other/x.md"}
        )
        self.assertFalse(Kwiz.objects.exists())

    def test_read_only_repo_cannot_push(self):
        _, repo = self.link_repo()
        self.client.post(
            reverse("repos:import", args=[repo.pk]), {"file_path": "kwizzes/sorting.md"}
        )
        link = LinkedFile.objects.get()
        self.client.post(reverse("repos:push", args=[link.pk]))
        self.assertEqual(self.gh.commits, [])

    def test_push_and_conflict(self):
        _, repo = self.link_repo(can_write="on")
        self.client.post(
            reverse("repos:import", args=[repo.pk]), {"file_path": "kwizzes/sorting.md"}
        )
        link = LinkedFile.objects.get()
        kwiz = link.kwiz
        kwiz.title = "Sorting, again"
        kwiz.save()
        self.client.post(reverse("repos:push", args=[link.pk]))
        self.assertIn("# Sorting, again", self.gh.files["kwizzes/sorting.md"])

        self.gh.files["kwizzes/sorting.md"] += (
            "\n## Someone else's kwestion\n- [x] yes\n"
        )
        response = self.client.post(reverse("repos:push", args=[link.pk]), follow=True)
        self.assertContains(response, "changed on GitHub")
        self.assertEqual(len(self.gh.commits), 1)

    def test_push_new_kwiz(self):
        _, repo = self.link_repo(can_write="on")
        kwiz = Kwiz.objects.create(owner=self.user, title="Graphs & Trees")
        self.client.post(reverse("repos:push_new", args=[repo.pk]), {"kwiz": kwiz.pk})
        self.assertIn("kwizzes/graphs-trees.md", self.gh.files)
        self.assertEqual(kwiz.repo_file.file_path, "kwizzes/graphs-trees.md")

    def test_failed_push_new_leaves_no_link(self):
        _, repo = self.link_repo(can_write="on")
        self.gh.writable = False
        kwiz = Kwiz.objects.create(owner=self.user, title="Graphs")
        self.client.post(reverse("repos:push_new", args=[repo.pk]), {"kwiz": kwiz.pk})
        self.assertFalse(LinkedFile.objects.exists())

    def test_other_users_cannot_touch_the_repo(self):
        _, repo = self.link_repo()
        other = User.objects.create_user(
            username="bob", email="bob@example.com", password="pw"
        )
        self.client.force_login(other)
        self.assertEqual(
            self.client.get(reverse("repos:detail", args=[repo.pk])).status_code, 404
        )
