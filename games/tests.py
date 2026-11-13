from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.sessions.backends.db import SessionStore
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from kahduke_config.asgi import application
from kwizzes.access import HOST_TOKENS, PLAYER_TOKENS
from kwizzes.models import Kwiz, PlayerScreen, RevealMode
from kwizzes.tests import make_kwiz

from .models import GameSession, Player, score
from .state import snapshot


class ScoringTests(TestCase):
    def test_speed_and_correctness(self):
        self.assertEqual(score(1000, 1, None, 20_000, 0), 1000)
        self.assertEqual(score(1000, 1, None, 20_000, 20_000), 500)
        self.assertEqual(score(1000, 1, None, 20_000, 10_000), 750)
        self.assertEqual(score(1000, 0.5, None, 20_000, 0), 500)
        self.assertEqual(score(1000, 0, None, 20_000, 0), 0)

    def test_choice_points_replace_correctness(self):
        self.assertEqual(score(1000, 0, 300, 20_000, 0), 300)
        self.assertEqual(score(1000, 1, 300, 20_000, 20_000), 150)

    def test_late_answers_are_clamped(self):
        self.assertEqual(score(1000, 1, None, 20_000, 25_000), 500)


class GameModelTests(TestCase):
    def setUp(self):
        self.kwiz = make_kwiz(kwestions=2)
        self.game = GameSession.start_for(self.kwiz)
        self.alice = Player.objects.create(game_session=self.game, nickname="alice")

    def choice(self, body):
        return self.game.current_slot().kwestion.choices.get(body=body)

    def test_phases(self):
        self.assertEqual(self.game.phase, "lobby")
        self.game.advance()
        self.assertEqual((self.game.phase, self.game.current_position), ("kwestion", 0))
        self.game.close_kwestion()
        self.assertEqual(self.game.phase, "reveal")
        self.game.advance()
        self.assertEqual((self.game.phase, self.game.current_position), ("kwestion", 1))
        self.game.close_kwestion()
        self.game.advance()
        self.assertEqual(self.game.phase, "finished")
        self.assertIsNotNone(self.game.ended_at)

    def test_answer_once_while_open(self):
        self.assertIsNone(self.alice.answer(1))  # lobby
        self.game.advance()
        right = self.choice("right")
        response = self.alice.answer(right.pk)
        self.assertGreater(response.points_awarded, 900)
        self.assertEqual(self.alice.total_score, response.points_awarded)
        self.assertIsNone(self.alice.answer(right.pk))

    def test_wrong_kwestions_choice_is_rejected(self):
        self.game.advance()
        other_kwestion = self.kwiz.ordered_slots()[1].kwestion
        self.assertIsNone(self.alice.answer(other_kwestion.choices.first().pk))

    def test_answer_after_time_limit_is_rejected(self):
        self.game.advance()
        self.game.kwestion_opened_at = timezone.now() - timedelta(seconds=30)
        self.game.save()
        self.assertIsNone(self.alice.answer(self.choice("right").pk))

    def test_shortcode_reusable_once_finished(self):
        self.game.end()
        reused = GameSession.objects.create(
            kwiz=self.kwiz, shortcode=self.game.shortcode
        )
        self.assertEqual(reused.shortcode, self.game.shortcode)


class RevealModeTests(TestCase):
    """Kwestions that don't reveal answers mustn't leak them any other way."""

    def play_first_kwestion(self, kwiz):
        game = GameSession.start_for(kwiz)
        alice = Player.objects.create(game_session=game, nickname="alice")
        game.advance()
        right = game.current_slot().kwestion.choices.get(body="right")
        response = alice.answer(right.pk)
        game.close_kwestion()
        return game, alice, response, right

    def test_full_reveals_correctness_counts_and_points(self):
        game, alice, response, right = self.play_first_kwestion(make_kwiz())
        self.assertGreater(response.points_awarded, 0)
        state = snapshot(game.pk, player=alice)
        self.assertEqual(state["reveal_mode"], "full")
        row = next(r for r in state["reveal"] if r["id"] == right.pk)
        self.assertEqual((row["correctness"], row["count"]), (1.0, 1))
        self.assertIn("points_awarded", state["me"])
        self.assertTrue(state["leaderboard"])

    def test_counts_only_sends_counts(self):
        kwiz = make_kwiz(reveal_mode=RevealMode.COUNTS)
        game, alice, response, right = self.play_first_kwestion(kwiz)
        self.assertEqual(response.points_awarded, 0)
        alice.refresh_from_db()
        self.assertEqual(alice.total_score, 0)
        for viewer in (None, alice):  # host, then player
            state = snapshot(game.pk, player=viewer)
            self.assertEqual(state["reveal_mode"], "counts")
            self.assertFalse(any("correctness" in r for r in state["reveal"]))
            self.assertEqual(
                {r["id"]: r["count"] for r in state["reveal"]}[right.pk], 1
            )
            self.assertNotIn("leaderboard", state)
        self.assertNotIn("points_awarded", state["me"])
        self.assertNotIn("score", state["me"])

    def test_hidden_sends_nothing(self):
        kwiz = make_kwiz(reveal_mode=RevealMode.HIDDEN)
        game, alice, _, _ = self.play_first_kwestion(kwiz)
        state = snapshot(game.pk, player=alice)
        self.assertEqual(state["reveal"], [])
        self.assertFalse(state["scored"])
        self.assertEqual(
            state["me"]["answered_choice_id"], alice.responses.get().answer_choice_id
        )
        game.end()
        state = snapshot(game.pk, player=alice)
        self.assertNotIn("leaderboard", state)
        self.assertNotIn("rank", state["me"])

    def test_one_hidden_kwestion_in_a_scored_kwiz(self):
        kwiz = make_kwiz()
        kwiz.slots.filter(position=0).update(reveal_mode=RevealMode.HIDDEN)
        game, alice, response, _ = self.play_first_kwestion(kwiz)
        self.assertEqual(response.points_awarded, 0)
        state = snapshot(game.pk, player=alice)
        self.assertEqual((state["reveal_mode"], state["reveal"]), ("hidden", []))
        # The leaderboard stays (later kwestions score) but hasn't moved.
        self.assertTrue(state["scored"])
        self.assertEqual(state["leaderboard"][0]["score"], 0)


class PlayerScreenTests(TestCase):
    """A kwiz can keep the kwestion, or the answers too, off players' phones."""

    def kwestion_for(self, player_screen):
        kwiz = make_kwiz(player_screen=player_screen)
        game = GameSession.start_for(kwiz)
        alice = Player.objects.create(game_session=game, nickname="alice")
        game.advance()
        return snapshot(game.pk)["kwestion"], snapshot(game.pk, player=alice)

    def test_full_shows_everything(self):
        host, player = self.kwestion_for(PlayerScreen.FULL)
        for key in ("prompt", "choices"):
            self.assertEqual(player["kwestion"][key], host[key])
        self.assertEqual(player["player_screen"], "full")

    def test_answers_only_drops_the_prompt(self):
        host, player = self.kwestion_for(PlayerScreen.ANSWERS)
        self.assertTrue(host["prompt"])
        self.assertEqual(player["kwestion"]["prompt"], "")
        self.assertEqual(player["kwestion"]["choices"], host["choices"])

    def test_shapes_only_drops_the_answer_text_too(self):
        host, player = self.kwestion_for(PlayerScreen.SHAPES)
        self.assertEqual(player["kwestion"]["prompt"], "")
        self.assertEqual(
            [c["id"] for c in player["kwestion"]["choices"]],
            [c["id"] for c in host["choices"]],
        )
        self.assertTrue(all(c["body"] == "" for c in player["kwestion"]["choices"]))
        self.assertTrue(all(c["body"] for c in host["choices"]))

    def test_remix_keeps_the_setting(self):
        kwiz = make_kwiz(player_screen=PlayerScreen.SHAPES)
        self.assertEqual(kwiz.remix().player_screen, PlayerScreen.SHAPES)


class GameViewTests(TestCase):
    def test_host_join_and_play_pages(self):
        kwiz = make_kwiz(visibility=Kwiz.Visibility.PUBLIC)
        response = self.client.post(reverse("games:start", args=[kwiz.pk]))
        game = GameSession.objects.get()
        self.assertRedirects(response, reverse("games:host", args=[game.shortcode]))

        player_client = self.client_class()
        response = player_client.post(
            reverse("join"), {"shortcode": game.shortcode, "nickname": "duke"}
        )
        self.assertRedirects(response, reverse("games:play", args=[game.shortcode]))
        response = player_client.post(
            reverse("join"), {"shortcode": game.shortcode, "nickname": "DUKE"}
        )
        self.assertContains(response, "already has that name")
        # A player can't open the host screen.
        response = player_client.get(reverse("games:host", args=[game.shortcode]))
        self.assertEqual(response.status_code, 404)

    def test_private_kwiz_cannot_be_hosted_by_strangers(self):
        kwiz = make_kwiz()
        response = self.client.post(reverse("games:start", args=[kwiz.pk]))
        self.assertEqual(response.status_code, 404)

    def test_game_api(self):
        game = GameSession.start_for(make_kwiz())
        data = self.client.get(f"/api/games/{game.shortcode}").json()
        self.assertEqual(data["kwiz_title"], "Sorting")
        game.end()
        self.assertEqual(
            self.client.get(f"/api/games/{game.shortcode}").status_code, 404
        )


def session_with(key, token):
    store = SessionStore()
    store[key] = [token]
    store.create()
    return store.session_key


class GameConsumerTests(TransactionTestCase):
    """Plays a whole one-kwestion game over WebSockets."""

    async def connect(self, session_key, shortcode):
        communicator = WebsocketCommunicator(
            application,
            f"/ws/games/{shortcode}/",
            headers=[
                (b"cookie", f"sessionid={session_key}".encode()),
                (b"origin", b"http://localhost"),
            ],
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        return communicator

    async def test_full_game(self):
        kwiz = await sync_to_async(make_kwiz)(kwestions=1)
        game = await sync_to_async(GameSession.start_for)(kwiz)
        player = await Player.objects.acreate(game_session=game, nickname="duke")
        host_key = await sync_to_async(session_with)(HOST_TOKENS, game.host_token)
        player_key = await sync_to_async(session_with)(
            PLAYER_TOKENS, player.session_token
        )

        host = await self.connect(host_key, game.shortcode)
        phone = await self.connect(player_key, game.shortcode)

        state = await host.receive_json_from()
        self.assertEqual((state["role"], state["phase"]), ("host", "lobby"))
        self.assertEqual(state["players"], [{"id": player.pk, "nickname": "duke"}])
        state = await phone.receive_json_from()
        self.assertEqual((state["role"], state["me"]["nickname"]), ("player", "duke"))

        await host.send_json_to({"action": "start"})
        state = await phone.receive_json_from()
        self.assertEqual(state["phase"], "kwestion")
        self.assertNotIn("correctness", state["kwestion"]["choices"][0])
        await host.receive_json_from()

        right = next(c for c in state["kwestion"]["choices"] if c["body"] == "right")
        await phone.send_json_to({"action": "answer", "choice_id": right["id"]})
        # Everyone has answered, so the kwestion closes straight to results.
        state = await phone.receive_json_from()
        self.assertEqual(state["phase"], "reveal")
        self.assertGreater(state["me"]["points_awarded"], 0)
        self.assertEqual(state["me"]["rank"], 1)
        state = await host.receive_json_from()
        counts = {c["id"]: c["count"] for c in state["reveal"]}
        self.assertEqual(counts[right["id"]], 1)

        # Players can't drive the game.
        await phone.send_json_to({"action": "next"})
        self.assertTrue(await phone.receive_nothing())

        await host.send_json_to({"action": "next"})
        state = await host.receive_json_from()
        self.assertEqual(state["phase"], "finished")
        self.assertEqual(state["leaderboard"][0]["nickname"], "duke")

        await host.disconnect()
        await phone.disconnect()

    async def test_strangers_are_rejected(self):
        kwiz = await sync_to_async(make_kwiz)()
        game = await sync_to_async(GameSession.start_for)(kwiz)
        communicator = WebsocketCommunicator(
            application,
            f"/ws/games/{game.shortcode}/",
            headers=[(b"origin", b"http://localhost")],
        )
        connected, code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(code, 4003)
