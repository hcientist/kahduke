from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from kwizzes.access import (
    HOST_TOKENS,
    PLAYER_TOKENS,
    can_edit_kwiz,
    can_host_game,
    player_for,
    remember_token,
)
from kwizzes.models import Kwiz

from .consumers import broadcast_update
from .forms import JoinForm
from .models import GameSession, Player


def _live_game(shortcode):
    game = GameSession.objects.active().filter(shortcode=shortcode).first()
    return game or get_object_or_404(GameSession, shortcode=shortcode)


@require_POST
def host(request, kwiz_id):
    """Start a new game of a kwiz. Anyone may host a public kwiz."""
    kwiz = get_object_or_404(Kwiz, pk=kwiz_id)
    if not (kwiz.is_public or can_edit_kwiz(request, kwiz)):
        raise Http404
    if errors := kwiz.playability_errors():
        for error in errors:
            messages.error(request, error)
        return redirect(kwiz)
    host_user = request.user if request.user.is_authenticated else None
    game = GameSession.start_for(kwiz, host_user=host_user)
    remember_token(request.session, HOST_TOKENS, game.host_token)
    return redirect("games:host", shortcode=game.shortcode)


def host_screen(request, shortcode):
    game = _live_game(shortcode)
    if not can_host_game(request, game):
        raise Http404
    join_url = request.build_absolute_uri(reverse("join"))
    share_url = f"{join_url}?{urlencode({'code': game.shortcode})}"
    return render(
        request,
        "games/host.html",
        {"game": game, "join_url": join_url, "share_url": share_url},
    )


def join(request):
    form = JoinForm(
        request.POST or None, initial={"shortcode": request.GET.get("code", "")}
    )
    if request.method == "POST" and form.is_valid():
        game = form.game
        player = Player.objects.create(
            game_session=game,
            nickname=form.cleaned_data["nickname"],
            user=request.user if request.user.is_authenticated else None,
        )
        remember_token(request.session, PLAYER_TOKENS, player.session_token)
        broadcast_update(game.pk)
        return redirect("games:play", shortcode=game.shortcode)
    context = {"form": form}
    code = request.GET.get("code", "").strip()
    if request.method == "GET" and code:
        # A shared invite link (/play/?code=123456): its link preview names
        # the kwiz, and the name box gets the focus since the code is filled.
        invite = GameSession.objects.active().filter(shortcode=code).first()
        if invite:
            form.fields["nickname"].widget.attrs["autofocus"] = True
            context |= {
                "invite": invite,
                "invite_title": f"Join “{invite.kwiz.title}” on KahDuke",
                "invite_description": (
                    f"Game code {invite.shortcode}. Tap the link, pick a name, "
                    "and play along; no account needed."
                ),
            }
    return render(request, "games/join.html", context)


def play_screen(request, shortcode):
    game = _live_game(shortcode)
    player = player_for(request, game)
    if player is None:
        return redirect(f"/play/?code={shortcode}")
    return render(request, "games/play.html", {"game": game, "player": player})
