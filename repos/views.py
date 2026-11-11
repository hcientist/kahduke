from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from kwizzes.markdown import MarkdownError
from kwizzes.models import Kwiz

from . import github, sync
from .forms import PushNewForm, RepoForm
from .models import LinkedFile, Repo


def _repo(request, pk):
    return get_object_or_404(Repo, pk=pk, owner=request.user)


def _link(request, pk):
    return get_object_or_404(
        LinkedFile.objects.select_related("repo", "kwiz"),
        pk=pk,
        repo__owner=request.user,
        kwiz__owner=request.user,
    )


def _report(request, error, file_path=""):
    where = f"{file_path}: " if file_path else ""
    if isinstance(error, MarkdownError):
        messages.error(request, f"{where}that file isn't a valid kwiz.")
        for problem in error.errors[:8]:
            messages.error(request, problem)
    else:
        messages.error(request, f"{where}{error}")


def _unlinked_kwizzes(user):
    return Kwiz.objects.filter(owner=user, repo_file__isnull=True)


@login_required
def repo_list(request):
    form = RepoForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        repo = form.save()
        messages.success(request, f"Linked {repo}.")
        return redirect("repos:detail", pk=repo.pk)
    repos = request.user.repos.all()
    return render(request, "repos/list.html", {"form": form, "repos": repos})


@login_required
def detail(request, pk):
    repo = _repo(request, pk)
    links = {link.file_path: link for link in repo.files.select_related("kwiz")}
    files, error = [], ""
    try:
        files = github.markdown_files(
            repo.full_name, repo.branch, repo.path, repo.token
        )
    except github.GitHubError as e:
        error = str(e)
    rows = [
        {
            "path": path,
            "url": repo.html_url(path),
            "name": path.removeprefix(f"{repo.path}/") if repo.path else path,
            "link": links.get(path),
            "changed": path in links and links[path].sha != sha,
        }
        for path, sha in files
    ]
    listed = {path for path, _ in files}
    missing = [link for path, link in links.items() if path not in listed]
    return render(
        request,
        "repos/detail.html",
        {
            "repo": repo,
            "rows": rows,
            "missing": missing,
            "error": error,
            "push_form": PushNewForm(kwizzes=_unlinked_kwizzes(request.user)),
        },
    )


@login_required
@require_POST
def import_file(request, pk):
    repo = _repo(request, pk)
    file_path = request.POST.get("file_path", "")
    prefix = f"{repo.path}/" if repo.path else ""
    if (
        not file_path.startswith(prefix)
        or repo.files.filter(file_path=file_path).exists()
    ):
        messages.error(request, "That file can't be imported.")
        return redirect("repos:detail", pk=repo.pk)
    try:
        kwiz = sync.import_file(repo, file_path, request.user)
    except (github.GitHubError, MarkdownError) as e:
        _report(request, e, file_path)
        return redirect("repos:detail", pk=repo.pk)
    messages.success(request, f"Imported “{kwiz.title}” from {file_path}.")
    return redirect(kwiz)


@login_required
@require_POST
def push_new(request, pk):
    repo = _repo(request, pk)
    form = PushNewForm(request.POST, kwizzes=_unlinked_kwizzes(request.user))
    if not repo.can_write:
        messages.error(request, "This repo is linked read-only.")
    elif not form.is_valid():
        messages.error(request, "Pick one of your kwizzes that isn't linked yet.")
    else:
        kwiz = form.cleaned_data["kwiz"]
        try:
            link = sync.push_new(repo, kwiz, form.cleaned_data["file_name"])
        except github.GitHubError as e:
            _report(request, e)
        else:
            messages.success(request, f"Committed “{kwiz.title}” as {link.file_path}.")
    return redirect("repos:detail", pk=repo.pk)


@login_required
@require_POST
def delete(request, pk):
    repo = _repo(request, pk)
    repo.delete()
    messages.success(request, f"Unlinked {repo}. Its kwizzes stay in KahDuke.")
    return redirect("repos:list")


@login_required
@require_POST
def pull(request, pk):
    link = _link(request, pk)
    try:
        sync.pull(link, request.user)
    except (github.GitHubError, MarkdownError) as e:
        _report(request, e, link.file_path)
    else:
        messages.success(request, f"Updated “{link.kwiz.title}” from {link.file_path}.")
    return redirect(link.kwiz)


@login_required
@require_POST
def push(request, pk):
    link = _link(request, pk)
    try:
        sync.push(link)
    except github.GitHubError as e:
        _report(request, e, link.file_path)
    else:
        messages.success(request, f"Committed “{link.kwiz.title}” to {link.file_path}.")
    return redirect(link.kwiz)


@login_required
@require_POST
def unlink(request, pk):
    link = _link(request, pk)
    link.delete()
    messages.success(request, f"“{link.kwiz.title}” is no longer linked to GitHub.")
    return redirect(link.kwiz)
