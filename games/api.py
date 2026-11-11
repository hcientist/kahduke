from ninja import Router
from ninja.errors import HttpError

from kwizzes.schemas import GameOut

from .models import GameSession

router = Router(tags=["games"])


@router.get("/games/{shortcode}", response=GameOut)
def get_game(request, shortcode: str):
    """Look up a joinable game by code, e.g. to validate the join form."""
    game = (
        GameSession.objects.active()
        .filter(shortcode=shortcode)
        .select_related("kwiz")
        .first()
    )
    if game is None:
        raise HttpError(404, "No game is running with that code.")
    return {
        "shortcode": game.shortcode,
        "kwiz_title": game.kwiz.title,
        "status": game.status,
        "player_count": game.players.count(),
    }
