"""The handful of GitHub REST calls KahDuke needs, over urllib.

Everything goes through `_call`, so tests can patch one function.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
TIMEOUT = 15


class GitHubError(Exception):
    def __init__(self, message, status=None):
        self.status = status
        super().__init__(message)


def _call(method, url, token="", body=None):
    request = urllib.request.Request(url, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "KahDuke")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
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
        401: "GitHub rejected the token. It may have expired or been revoked.",
        403: "The token isn't allowed to do that. For writing, it needs "
        "Contents: Read and write on this repo.",
        404: "GitHub says that doesn't exist, or the token can't see it. "
        "Private repos need a token with access to that repo.",
        409: "The file changed on GitHub since KahDuke last saw it. "
        "Pull it first, then push again.",
        422: "GitHub refused the change.",
    }
    message = hints.get(status, f"GitHub answered {status}.")
    return f"{message} ({detail})" if detail else message


def _q(path):
    return urllib.parse.quote(path, safe="/")


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
