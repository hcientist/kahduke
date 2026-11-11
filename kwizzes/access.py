"""Who may do what, for both signed-in users and anonymous visitors.

Anonymous creators, hosts and players each hold a secret token. The tokens
they earn are remembered in their Django session under the keys below, so
the browser that made a kwiz (or hosted or joined a game) keeps access
without an account. On sign-in, accounts.signals claims every row whose
token is in the session.
"""

KWIZ_TOKENS = "kwiz_edit_tokens"
HOST_TOKENS = "host_tokens"
PLAYER_TOKENS = "player_tokens"


def session_tokens(session, key):
    return list(session.get(key, []))


def remember_token(session, key, token):
    tokens = session_tokens(session, key)
    if token not in tokens:
        tokens.append(token)
        session[key] = tokens


def _user(request_or_scope):
    if isinstance(request_or_scope, dict):
        return request_or_scope.get("user")
    return request_or_scope.user


def can_edit_kwiz(request, kwiz):
    user = request.user
    if user.is_authenticated and kwiz.owner_id == user.pk:
        return True
    return kwiz.edit_token in session_tokens(request.session, KWIZ_TOKENS)


def can_view_kwiz(request, kwiz):
    return kwiz.is_public or can_edit_kwiz(request, kwiz)


def can_host_game(request_or_scope, game):
    """Works with an HTTP request or a Channels scope (both carry a session)."""
    user = _user(request_or_scope)
    if user is not None and user.is_authenticated and game.host_user_id == user.pk:
        return True
    session = (
        request_or_scope["session"]
        if isinstance(request_or_scope, dict)
        else request_or_scope.session
    )
    return game.host_token in session_tokens(session, HOST_TOKENS)


def player_for(request_or_scope, game):
    """The Player this browser joined `game` as, or None."""
    session = (
        request_or_scope["session"]
        if isinstance(request_or_scope, dict)
        else request_or_scope.session
    )
    tokens = session_tokens(session, PLAYER_TOKENS)
    return game.players.filter(session_token__in=tokens).first()
