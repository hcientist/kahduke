from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from games.models import GameSession, Player
from kwizzes.access import HOST_TOKENS, KWIZ_TOKENS, PLAYER_TOKENS, session_tokens
from kwizzes.models import Kwestion, Kwiz


@receiver(user_logged_in)
def claim_anonymous_work(sender, request, user, **kwargs):
    """Give a user who just signed in (or signed up) everything they made
    anonymously in this browser: kwizzes and their kwestions, hosted games,
    and games they played."""
    session = request.session

    kwizzes = Kwiz.objects.filter(
        owner=None, edit_token__in=session_tokens(session, KWIZ_TOKENS)
    )
    Kwestion.objects.filter(owner=None, slots__kwiz__in=kwizzes).update(owner=user)
    kwizzes.update(owner=user)

    GameSession.objects.filter(
        host_user=None, host_token__in=session_tokens(session, HOST_TOKENS)
    ).update(host_user=user)
    Player.objects.filter(
        user=None, session_token__in=session_tokens(session, PLAYER_TOKENS)
    ).update(user=user)
