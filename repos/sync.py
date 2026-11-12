"""Moving kwizzes between KahDuke and a linked GitHub repo."""

import logging

from django.db import transaction
from django.utils import timezone

from kwizzes import markdown
from kwizzes.editing import save_kwiz
from kwizzes.models import Kwiz

from . import github
from .models import LinkedFile, Repo

log = logging.getLogger(__name__)


def list_files(repo):
    return github.markdown_files(repo.full_name, repo.branch, repo.path, repo.token())


def _read(repo, file_path):
    return github.read_file(repo.full_name, repo.branch, file_path, repo.token())


@transaction.atomic
def import_file(repo, file_path, user):
    """Make a new kwiz from a repo file and link the two. Returns the kwiz."""
    text, sha = _read(repo, file_path)
    data = markdown.parse(text)
    kwiz = save_kwiz(Kwiz(owner=user), data, user)
    LinkedFile.objects.create(
        kwiz=kwiz, repo=repo, file_path=file_path, sha=sha, synced_at=timezone.now()
    )
    return kwiz


@transaction.atomic
def pull(link, user):
    """Replace the kwiz with the file's current contents."""
    text, sha = _read(link.repo, link.file_path)
    data = markdown.match_existing(link.kwiz, markdown.parse(text))
    save_kwiz(link.kwiz, data, user)
    link.sha = sha
    link.synced_at = timezone.now()
    link.save(update_fields=["sha", "synced_at"])
    return link.kwiz


def push(link):
    """Commit the kwiz to its file. GitHub rejects it if the file changed."""
    repo = link.repo
    token = repo.token(write=True)
    verb = "Update" if link.sha else "Add"
    link.sha = github.write_file(
        repo.full_name,
        repo.branch,
        link.file_path,
        markdown.render(link.kwiz),
        f"{verb} kwiz “{link.kwiz.title}” from KahDuke",
        sha=link.sha,
        token=token,
    )
    link.synced_at = timezone.now()
    if link.pk:
        link.save(update_fields=["sha", "synced_at"])
    else:
        link.save()
    return link


def push_new(repo, kwiz, file_name=""):
    """Link a kwiz to a new file in the repo's folder and commit it."""
    name = file_name.strip().strip("/") or markdown.filename(kwiz)
    if not name.lower().endswith(".md"):
        name += ".md"
    file_path = f"{repo.path}/{name}" if repo.path else name
    link = LinkedFile(kwiz=kwiz, repo=repo, file_path=file_path)
    push(link)  # saves the link only once GitHub has the file
    return link


def on_push(payload):
    """Webhook: pull every linked kwiz whose file this push added or changed.

    Returns how many kwizzes were updated. A file that no longer parses is
    logged and skipped; its kwiz keeps its last good version.
    """
    ref = payload.get("ref", "")
    if not ref.startswith("refs/heads/"):
        return 0
    branch = ref.removeprefix("refs/heads/")
    full_name = payload.get("repository", {}).get("full_name", "")
    installation_id = payload.get("installation", {}).get("id")
    changed = set()
    for commit in payload.get("commits", []):
        changed.update(commit.get("added", []))
        changed.update(commit.get("modified", []))
    links = LinkedFile.objects.filter(
        repo__in=Repo.objects.filter(
            full_name__iexact=full_name,
            branch=branch,
            installation__installation_id=installation_id,
        ),
        file_path__in=changed,
    ).select_related("repo__installation", "kwiz__owner")
    updated = 0
    for link in links:
        try:
            pull(link, link.kwiz.owner)
        except (github.GitHubError, markdown.MarkdownError) as e:
            log.warning("Webhook pull of %s failed: %s", link, e)
        else:
            updated += 1
    return updated
