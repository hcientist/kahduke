"""What each screen should show right now.

Clients never track game state themselves. After any change the consumers
broadcast "something changed", and each connection rebuilds its own snapshot
here: hosts see answer counts and correctness, players see only their own
result. Reconnecting is just asking for a fresh snapshot.

A kwestion's reveal mode limits what anyone sees once it closes. "counts"
sends how many picked each choice but no correctness; "hidden" sends neither,
and neither one sends points, since those would show who was right. What a
screen isn't allowed to show is never sent, so it can't be read off the socket.

The kwiz's player screen setting works the same way: phones get the prompt
and the answer text only if the kwiz says so; otherwise players read them off
the projector and tap a shape.
"""

from django.db.models import Count

from kwizzes.models import PlayerScreen, RevealMode

from .models import GameSession

LEADERBOARD_SIZE = 5


def _kwestion(game, slot):
    kwestion = slot.kwestion
    remaining_ms = None
    if game.kwestion_opened_at:
        remaining_ms = max(slot.time_limit_sec * 1000 - game.ms_since_open(), 0)
    return {
        "position": slot.position,
        "number": game.slots().filter(position__lt=slot.position).count() + 1,
        "total": game.slots().count(),
        "prompt": kwestion.prompt,
        "media_url": kwestion.media_url,
        "time_limit_sec": slot.time_limit_sec,
        "remaining_ms": remaining_ms,
        "choices": [
            {"id": c.id, "body": c.body, "media_url": c.media_url}
            for c in kwestion.choices.all()
        ],
    }


def _for_phone(kwestion, player_screen):
    """Strip what the kwiz keeps off players' phones."""
    if player_screen == PlayerScreen.FULL:
        return kwestion
    kwestion = kwestion | {"prompt": "", "media_url": ""}
    if player_screen == PlayerScreen.SHAPES:
        kwestion["choices"] = [
            {"id": c["id"], "body": "", "media_url": ""} for c in kwestion["choices"]
        ]
    return kwestion


def _reveal(slot, mode):
    if mode == RevealMode.HIDDEN:
        return []
    counts = dict(slot.responses.values_list("answer_choice").annotate(n=Count("id")))
    rows = []
    for c in slot.kwestion.choices.all():
        row = {"id": c.id, "count": counts.get(c.id, 0)}
        if mode == RevealMode.FULL:
            row["correctness"] = float(c.correctness)
        rows.append(row)
    return rows


def _leaderboard(game):
    return [
        {"id": p.id, "nickname": p.nickname, "score": p.total_score}
        for p in game.leaderboard(LEADERBOARD_SIZE)
    ]


def snapshot(game_id, player=None):
    game = GameSession.objects.select_related("kwiz").get(pk=game_id)
    phase = game.phase
    players = game.players.all()
    data = {
        "shortcode": game.shortcode,
        "kwiz_title": game.kwiz.title,
        "phase": phase,
        "player_count": players.count(),
        # False when no kwestion in the kwiz reveals answers, so every score
        # is zero and there is no leaderboard to show.
        "scored": game.kwiz.has_scored_slots,
        "player_screen": game.kwiz.player_screen,
    }

    if phase == "lobby":
        data["players"] = [{"id": p.id, "nickname": p.nickname} for p in players]

    slot = game.current_slot() if phase in ("kwestion", "reveal") else None
    if slot is not None:
        data["kwestion"] = _kwestion(game, slot)
        data["answered"] = slot.responses.count()
        data["reveal_mode"] = slot.effective_reveal_mode
    if phase == "reveal" and slot is not None:
        data["reveal"] = _reveal(slot, slot.effective_reveal_mode)
    if phase in ("reveal", "finished") and data["scored"]:
        data["leaderboard"] = _leaderboard(game)

    if player is not None:
        if "kwestion" in data:
            data["kwestion"] = _for_phone(data["kwestion"], game.kwiz.player_screen)
        player.refresh_from_db()
        me = {"id": player.id, "nickname": player.nickname}
        if data["scored"]:
            me["score"] = player.total_score
        if slot is not None:
            response = slot.responses.filter(player=player).first()
            me["answered_choice_id"] = response.answer_choice_id if response else None
            if phase == "reveal" and slot.is_scored:
                me["points_awarded"] = response.points_awarded if response else 0
        if phase in ("reveal", "finished") and data["scored"]:
            me["rank"] = (
                game.players.filter(total_score__gt=player.total_score).count() + 1
            )
        data["me"] = me

    return data
