import json
import logging
import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotFound,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from kwizzes.markdown import MarkdownError
from kwizzes.models import Kwiz

from . import github, sync
from .forms import PushNewForm, RepoForm
from .models import Installation, LinkedFile, Repo

log = logging.getLogger(__name__)
OAUTH_STATE = "github_oauth_state"


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
    repos = request.user.repos.select_related("installation")
    installations = list(request.user.github_installations.all())
    for installation in installations:
        try:
            installation.repo_names = github.installation_repos(
                installation.installation_id
            )
        except github.GitHubError as e:
            installation.error = str(e)
    return render(
        request,
        "repos/list.html",
        {
            "form": form,
            "repos": repos,
            "installations": installations,
            "app_enabled": github.app_enabled(),
        },
    )


@login_required
def detail(request, pk):
    repo = _repo(request, pk)
    links = {link.file_path: link for link in repo.files.select_related("kwiz")}
    files, error = [], ""
    try:
        files = sync.list_files(repo)
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


# --- The GitHub App: install, sign in, webhook ---------------------------


def _callback_uri(request):
    return request.build_absolute_uri(reverse("repos:github_callback"))


def _new_state(request):
    state = secrets.token_urlsafe(24)
    request.session[OAUTH_STATE] = state
    return state


@login_required
def github_install(request):
    """Send the user to GitHub to install the app on chosen repos.

    The app asks GitHub to have the user authorize it during installation,
    so GitHub comes back to github_callback with an OAuth code.
    """
    if not github.app_enabled():
        raise Http404
    return redirect(github.install_url(_new_state(request)))


@login_required
def github_connect(request):
    """Sign in with GitHub to pick up installations that already exist."""
    if not github.app_enabled():
        raise Http404
    return redirect(github.authorize_url(_new_state(request), _callback_uri(request)))


@login_required
def github_callback(request):
    """Learn which installations this user can access, as GitHub says.

    The installation_id GitHub appends to the URL is never trusted on its
    own (anyone could type one in); only /user/installations, asked with
    the user's own OAuth token, decides.
    """
    if not github.app_enabled():
        raise Http404
    code = request.GET.get("code", "")
    state = request.GET.get("state", "")
    expected = request.session.pop(OAUTH_STATE, None)
    if not code or not expected or not secrets.compare_digest(state, expected):
        if request.GET.get("installation_id") and code:
            # Installs started on GitHub's site carry no state of ours; sign
            # in again (GitHub skips the prompt) to get a code we can trust.
            return redirect("repos:github_connect")
        messages.error(request, "That GitHub sign-in didn't come from here. Try again.")
        return redirect("repos:list")
    try:
        user_token = github.exchange_code(code, _callback_uri(request))
        found = github.user_installations(user_token)
    except github.GitHubError as e:
        messages.error(request, str(e))
        return redirect("repos:list")

    mine = []
    for data in found:
        installation, _ = Installation.objects.update_or_create(
            installation_id=data["id"],
            defaults={
                "account_login": data["account"]["login"],
                "account_type": data["account"].get("type", ""),
            },
        )
        mine.append(installation)
    request.user.github_installations.set(mine)
    if mine:
        names = ", ".join(i.account_login for i in mine)
        messages.success(request, f"KahDuke can use its GitHub App on: {names}.")
    else:
        messages.warning(
            request, "The KahDuke app isn't installed anywhere you can see."
        )
    return redirect("repos:list")


@csrf_exempt
@require_POST
def github_webhook(request):
    if not github.app_settings()["WEBHOOK_SECRET"]:
        return HttpResponseNotFound()
    if not github.verify_webhook(
        request.body, request.headers.get("X-Hub-Signature-256", "")
    ):
        return HttpResponse(status=401)
    try:
        payload = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest()
    event = request.headers.get("X-GitHub-Event", "")
    if event == "push":
        updated = sync.on_push(payload)
        return HttpResponse(f"updated {updated}")
    if event == "installation" and payload.get("action") == "deleted":
        Installation.objects.filter(
            installation_id=payload["installation"]["id"]
        ).delete()
    return HttpResponse("ok")
