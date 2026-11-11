from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from games.forms import JoinForm
from games.models import GameSession, Player

from . import markdown
from .access import (
    HOST_TOKENS,
    KWIZ_TOKENS,
    PLAYER_TOKENS,
    can_edit_kwiz,
    can_view_kwiz,
    remember_token,
    session_tokens,
)
from .editing import save_kwiz
from .forms import KwizCreateForm, MarkdownImportForm
from .models import Kwestion, Kwiz


def _viewable_kwiz(request, pk):
    kwiz = get_object_or_404(Kwiz.objects.select_related("owner"), pk=pk)
    if not can_view_kwiz(request, kwiz):
        raise Http404
    return kwiz


def _editable_kwiz(request, pk):
    kwiz = get_object_or_404(Kwiz, pk=pk)
    if not can_edit_kwiz(request, kwiz):
        raise Http404
    return kwiz


def home(request):
    featured = (
        Kwiz.objects.filter(visibility=Kwiz.Visibility.PUBLIC)
        .select_related("owner")
        .annotate(kwestion_count=Count("slots"))[:6]
    )
    return render(
        request,
        "home.html",
        {"join_form": JoinForm(), "featured": featured},
    )


def dashboard(request):
    """Everything this visitor owns: by account, or by tokens in this browser."""
    session = request.session
    user = request.user
    mine = Q(edit_token__in=session_tokens(session, KWIZ_TOKENS))
    hosted = Q(host_token__in=session_tokens(session, HOST_TOKENS))
    played = Q(session_token__in=session_tokens(session, PLAYER_TOKENS))
    if user.is_authenticated:
        mine |= Q(owner=user)
        hosted |= Q(host_user=user)
        played |= Q(user=user)

    kwizzes = Kwiz.objects.filter(mine).annotate(kwestion_count=Count("slots"))
    games = GameSession.objects.filter(hosted).select_related("kwiz")[:10]
    plays = (
        Player.objects.filter(played)
        .select_related("game_session__kwiz")
        .order_by("-joined_at")[:10]
    )
    return render(
        request,
        "kwizzes/dashboard.html",
        {"kwizzes": kwizzes, "games": games, "plays": plays},
    )


def library(request):
    q = request.GET.get("q", "").strip()
    kwizzes = (
        Kwiz.objects.filter(visibility=Kwiz.Visibility.PUBLIC)
        .select_related("owner", "remixed_from")
        .annotate(kwestion_count=Count("slots"), remix_count=Count("remixes"))
    )
    if q:
        kwizzes = kwizzes.filter(Q(title__icontains=q) | Q(description__icontains=q))
    return render(request, "kwizzes/library.html", {"kwizzes": kwizzes, "q": q})


def create(request):
    form = KwizCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        kwiz = form.save(commit=False)
        if request.user.is_authenticated:
            kwiz.owner = request.user
        kwiz.save()
        remember_token(request.session, KWIZ_TOKENS, kwiz.edit_token)
        return redirect("kwizzes:edit", pk=kwiz.pk)
    return render(request, "kwizzes/create.html", {"form": form})


def detail(request, pk):
    kwiz = _viewable_kwiz(request, pk)
    slots = kwiz.ordered_slots().prefetch_related("kwestion__choices")
    return render(
        request,
        "kwizzes/detail.html",
        {
            "kwiz": kwiz,
            "slots": slots,
            "can_edit": can_edit_kwiz(request, kwiz),
            "errors": kwiz.playability_errors(),
            "edit_link": request.build_absolute_uri(
                reverse("kwizzes:edit_by_token", args=[kwiz.edit_token])
            ),
        },
    )


def edit(request, pk):
    kwiz = _editable_kwiz(request, pk)
    return render(request, "kwizzes/edit.html", {"kwiz": kwiz})


def edit_by_token(request, token):
    """The secret link anonymous creators keep to edit from another browser."""
    kwiz = get_object_or_404(Kwiz, edit_token=token)
    remember_token(request.session, KWIZ_TOKENS, kwiz.edit_token)
    return redirect("kwizzes:edit", pk=kwiz.pk)


@require_POST
def remix(request, pk):
    kwiz = _viewable_kwiz(request, pk)
    owner = request.user if request.user.is_authenticated else None
    copy = kwiz.remix(owner=owner)
    remember_token(request.session, KWIZ_TOKENS, copy.edit_token)
    messages.success(request, f"Remixed “{kwiz.title}”. This copy is yours to change.")
    return redirect("kwizzes:edit", pk=copy.pk)


@require_POST
def delete(request, pk):
    kwiz = _editable_kwiz(request, pk)
    title = kwiz.title
    kwiz.delete()
    messages.success(request, f"Deleted “{title}”.")
    return redirect("kwizzes:dashboard")


@login_required
def bank(request):
    q = request.GET.get("q", "").strip()
    kwestions = Kwestion.objects.filter(owner=request.user).prefetch_related(
        "choices", "slots__kwiz"
    )
    if q:
        kwestions = kwestions.filter(prompt__icontains=q)
    return render(request, "kwizzes/bank.html", {"kwestions": kwestions, "q": q})


def export_markdown(request, pk):
    kwiz = _viewable_kwiz(request, pk)
    response = HttpResponse(
        markdown.render(kwiz), content_type="text/markdown; charset=utf-8"
    )
    if "download" in request.GET:
        response["Content-Disposition"] = (
            f'attachment; filename="{markdown.filename(kwiz)}"'
        )
    return response


def import_markdown(request, pk=None):
    """Make a new kwiz from Markdown, or (with pk) replace an existing one's."""
    kwiz = _editable_kwiz(request, pk) if pk else None
    initial = {"markdown": markdown.render(kwiz)} if kwiz else {}
    form = MarkdownImportForm(
        request.POST or None, request.FILES or None, initial=initial
    )
    problems = []
    if request.method == "POST" and form.is_valid():
        try:
            data = markdown.parse(form.cleaned_data["markdown"])
        except markdown.MarkdownError as e:
            problems = e.errors
        else:
            owner = request.user if request.user.is_authenticated else None
            target = kwiz or Kwiz(owner=owner)
            if kwiz:
                markdown.match_existing(kwiz, data)
            try:
                save_kwiz(target, data, request.user)
            except (PermissionDenied, ValidationError) as e:
                problems = e.messages if hasattr(e, "messages") else [str(e)]
            else:
                remember_token(request.session, KWIZ_TOKENS, target.edit_token)
                messages.success(request, f"Imported “{target.title}”.")
                return redirect(target)
    return render(
        request,
        "kwizzes/import.html",
        {"form": form, "kwiz": kwiz, "problems": problems},
    )
