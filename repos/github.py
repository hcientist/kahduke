"""The GitHub REST calls KahDuke needs, over urllib.

Two kinds of credentials:

- As the GitHub App: a JWT signed with the app's private key, traded for an
  installation token that lasts an hour. KahDuke asks for each token to cover
  one repo and only the Contents permission it needs right now.
- As a GitHub user, briefly: the OAuth code from "Sign in with GitHub" is
  traded for a user token, used once to list the installations that user can
  see, and then dropped.

Every request goes through `_call`, so tests can patch one function.
"""

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt
from django.conf import settings
from django.core.cache import cache

API = "https://api.github.com"
WEB = "https://github.com"
TIMEOUT = 15


class GitHubError(Exception):
    def __init__(self, message, status=None):
        self.status = status
        super().__init__(message)


def app_settings():
    return settings.GITHUB_APP


def app_enabled():
    conf = app_settings()
    return all(conf[k] for k in ("SLUG", "CLIENT_ID", "CLIENT_SECRET", "PRIVATE_KEY"))


def _call(method, url, token="", body=None, auth="Bearer"):
    request = urllib.request.Request(url, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "KahDuke")
    if token:
        request.add_header("Authorization", f"{auth} {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, data=data, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get("message", "")
        except ValueError:
            detail = ""
        raise GitHubError(_explain(e.code, detail), status=e.code) from e
    except urllib.error.URLError as e:
        raise GitHubError(f"Couldn't reach GitHub: {e.reason}") from e


def _explain(status, detail):
    hints = {
        401: "GitHub didn't accept KahDuke's credentials.",
        403: "GitHub didn't allow that. If KahDuke should commit to this repo, "
        "make sure the app's installation includes it.",
        404: "GitHub says that doesn't exist, or KahDuke can't see it. For a "
        "private repo, install the KahDuke GitHub App on it.",
        409: "The file changed on GitHub since KahDuke last saw it. "
        "Pull it first, then commit again.",
        422: "GitHub refused the request.",
    }
    message = hints.get(status, f"GitHub answered {status}.")
    return f"{message} ({detail})" if detail else message


def _q(path):
    return urllib.parse.quote(path, safe="/")


# --- As the app -------------------------------------------------------------


def app_jwt():
    conf = app_settings()
    now = int(time.time())
    claims = {"iat": now - 60, "exp": now + 540, "iss": conf["CLIENT_ID"]}
    return jwt.encode(claims, conf["PRIVATE_KEY"], algorithm="RS256")


def installation_token(installation_id, repo_name=None, write=False):
    """An installation token, cached until shortly before it expires.

    With `repo_name`, the token covers only that repo, and only Contents
    read (or write) plus the Metadata read every token gets.
    """
    access = "write" if write else "read"
    key = f"gh-token:{installation_id}:{repo_name or '*'}:{access}"
    if token := cache.get(key):
        return token
    body = {"permissions": {"contents": access, "metadata": "read"}}
    if repo_name:
        body["repositories"] = [repo_name]
    data = _call(
        "POST",
        f"{API}/app/installations/{installation_id}/access_tokens",
        app_jwt(),
        body,
    )
    cache.set(key, data["token"], 50 * 60)  # GitHub's tokens last 60 minutes
    return data["token"]


def installation_repos(installation_id):
    """Full names of the repos an installation covers (first 300)."""
    token = installation_token(installation_id)
    names = []
    for page in (1, 2, 3):
        data = _call(
            "GET", f"{API}/installation/repositories?per_page=100&page={page}", token
        )
        names += [r["full_name"] for r in data.get("repositories", [])]
        if len(names) >= data.get("total_count", 0):
            break
    return names


def install_url(state):
    slug = app_settings()["SLUG"]
    return f"{WEB}/apps/{slug}/installations/new?" + urllib.parse.urlencode(
        {"state": state}
    )


def verify_webhook(body, signature):
    secret = app_settings()["WEBHOOK_SECRET"]
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# --- As the signed-in user, briefly -----------------------------------------


def authorize_url(state, redirect_uri):
    return f"{WEB}/login/oauth/authorize?" + urllib.parse.urlencode(
        {
            "client_id": app_settings()["CLIENT_ID"],
            "state": state,
            "redirect_uri": redirect_uri,
        }
    )


def exchange_code(code, redirect_uri):
    conf = app_settings()
    data = _call(
        "POST",
        f"{WEB}/login/oauth/access_token",
        body={
            "client_id": conf["CLIENT_ID"],
            "client_secret": conf["CLIENT_SECRET"],
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    if "access_token" not in data:
        raise GitHubError(
            data.get("error_description") or "GitHub didn't finish signing you in."
        )
    return data["access_token"]


def user_installations(user_token):
    """This app's installations that the token's user can access."""
    installs = []
    for page in (1, 2, 3):
        data = _call(
            "GET", f"{API}/user/installations?per_page=100&page={page}", user_token
        )
        installs += data.get("installations", [])
        if len(installs) >= data.get("total_count", 0):
            break
    return installs


# --- Repo contents ----------------------------------------------------------


def repo_info(full_name, token=""):
    return _call("GET", f"{API}/repos/{full_name}", token)


def markdown_files(full_name, branch, folder, token=""):
    """Every .md file under `folder` on `branch`, as (path, sha) pairs."""
    tree = _call(
        "GET",
        f"{API}/repos/{full_name}/git/trees/{_q(branch)}?recursive=1",
        token,
    )
    prefix = f"{folder}/" if folder else ""
    return [
        (item["path"], item["sha"])
        for item in tree.get("tree", [])
        if item["type"] == "blob"
        and item["path"].lower().endswith(".md")
        and item["path"].startswith(prefix)
        and item["path"].rsplit("/", 1)[-1].lower() != "readme.md"
    ]


def read_file(full_name, branch, path, token=""):
    """Returns (text, sha)."""
    data = _call(
        "GET",
        f"{API}/repos/{full_name}/contents/{_q(path)}?ref={urllib.parse.quote(branch)}",
        token,
    )
    if isinstance(data, list) or data.get("type") != "file":
        raise GitHubError(f"{path} isn't a file.")
    return base64.b64decode(data["content"]).decode("utf-8-sig"), data["sha"]


def write_file(full_name, branch, path, text, message, sha="", token=""):
    """Create or update a file. Returns the new blob sha."""
    body = {
        "message": message,
        "content": base64.b64encode(text.encode()).decode(),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    data = _call("PUT", f"{API}/repos/{full_name}/contents/{_q(path)}", token, body)
    return data["content"]["sha"]
