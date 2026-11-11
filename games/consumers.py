from asgiref.sync import async_to_sync
from channels.generic.websocket import JsonWebsocketConsumer

from kwizzes.access import can_host_game, player_for

from .models import GameSession, Player
from .state import snapshot


def group_name(game_id):
    return f"game_{game_id}"


def broadcast_update(game_id):
    """Tell every screen in a game to refresh its snapshot."""
    from channels.layers import get_channel_layer

    async_to_sync(get_channel_layer().group_send)(
        group_name(game_id), {"type": "game.update"}
    )


class GameConsumer(JsonWebsocketConsumer):
    """One socket per screen. The URL picks the game; the session decides
    whether this screen is the host, a player, or neither (rejected).

    Host sends:   {"action": "start" | "close" | "next" | "end"}
    Player sends: {"action": "answer", "choice_id": <id>}
    Server sends: the snapshot from games.state, after every change.
    """

    def connect(self):
        shortcode = self.scope["url_route"]["kwargs"]["shortcode"]
        game = GameSession.objects.active().filter(shortcode=shortcode).first()
        if game is None:
            # Finished games have no live screens; their codes may be reused.
            game = GameSession.objects.filter(shortcode=shortcode).first()
        if game is None:
            self.close(code=4004)
            return

        self.game_id = game.pk
        self.is_host = can_host_game(self.scope, game)
        self.player = None if self.is_host else player_for(self.scope, game)
        if not self.is_host and self.player is None:
            self.close(code=4003)
            return

        async_to_sync(self.channel_layer.group_add)(
            group_name(self.game_id), self.channel_name
        )
        self.accept()
        self.send_snapshot()

    def disconnect(self, close_code):
        if hasattr(self, "game_id"):
            async_to_sync(self.channel_layer.group_discard)(
                group_name(self.game_id), self.channel_name
            )

    def receive_json(self, content, **kwargs):
        action = content.get("action")
        if self.is_host:
            changed = self.host_action(action)
        elif action == "answer":
            changed = self.player_answer(content.get("choice_id"))
        else:
            changed = False
        if changed:
            broadcast_update(self.game_id)

    def host_action(self, action):
        game = GameSession.objects.get(pk=self.game_id)
        match (action, game.phase):
            case ("start", "lobby") if game.players.exists():
                game.advance()
            case ("close", "kwestion"):
                game.close_kwestion()
            case ("next", "reveal"):
                game.advance()
            case ("end", phase) if phase != "finished":
                game.end()
            case _:
                return False
        return True

    def player_answer(self, choice_id):
        player = Player.objects.get(pk=self.player.pk)
        if player.answer(choice_id) is None:
            return False
        # Close the kwestion early once everyone has answered.
        game = GameSession.objects.get(pk=self.game_id)
        slot = game.current_slot()
        if slot and slot.responses.count() >= game.players.count():
            game.close_kwestion()
        return True

    def game_update(self, event):
        self.send_snapshot()

    def send_snapshot(self):
        self.send_json(
            {"role": "host" if self.is_host else "player"}
            | snapshot(self.game_id, player=self.player)
        )
