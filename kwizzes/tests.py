import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from games.models import GameSession, Player

from .access import HOST_TOKENS, KWIZ_TOKENS, PLAYER_TOKENS
from .models import AnswerChoice, Kwestion, Kwiz, KwizKwestion, RevealMode

User = get_user_model()


def make_kwiz(owner=None, kwestions=2, **kwargs):
    kwiz = Kwiz.objects.create(owner=owner, title="Sorting", **kwargs)
    for position in range(kwestions):
        kwestion = Kwestion.objects.create(owner=owner, prompt=f"Q{position}")
        AnswerChoice.objects.create(
            kwestion=kwestion, position=0, body="right", correctness=1
        )
        AnswerChoice.objects.create(
            kwestion=kwestion, position=1, body="wrong", correctness=0
        )
        KwizKwestion.objects.create(kwiz=kwiz, kwestion=kwestion, position=position)
    return kwiz


def payload_for(kwiz_json):
    """Turn a GET /api/kwizzes/{id} response back into a PUT body."""
    return {
        "title": kwiz_json["title"],
        "description": kwiz_json["description"],
        "visibility": kwiz_json["visibility"],
        "reveal_mode": kwiz_json["reveal_mode"],
        "slots": [
            {
                "id": s["id"],
                "kwestion_id": s["kwestion_id"],
                "points_override": s["points_override"],
                "time_limit_override_sec": s["time_limit_override_sec"],
                "reveal_mode": s["reveal_mode"],
                "kwestion": {
                    k: s["kwestion"][k]
                    for k in (
                        "prompt",
                        "media_url",
                        "scoring_mode",
                        "default_points",
                        "default_time_limit_sec",
                    )
                }
                | {
                    "choices": [
                        {
                            k: c[k]
                            for k in (
                                "id",
                                "body",
                                "media_url",
                                "correctness",
                                "points",
                            )
                        }
                        for c in s["kwestion"]["choices"]
                    ]
                },
            }
            for s in kwiz_json["slots"]
        ],
    }


class KwizModelTests(TestCase):
    def test_playability(self):
        kwiz = Kwiz.objects.create(title="Empty")
        self.assertEqual(kwiz.playability_errors(), ["Add at least one kwestion."])
        kwestion = Kwestion.objects.create(prompt="No choices")
        KwizKwestion.objects.create(kwiz=kwiz, kwestion=kwestion, position=0)
        self.assertEqual(
            kwiz.playability_errors(), ["Kwestion 1 needs an answer choice."]
        )
        AnswerChoice.objects.create(kwestion=kwestion, position=0, body="a")
        self.assertEqual(kwiz.playability_errors(), [])

    def test_slot_overrides_fall_back_to_kwestion_defaults(self):
        kwiz = make_kwiz(kwestions=1)
        slot = kwiz.slots.get()
        self.assertEqual((slot.points, slot.time_limit_sec), (1000, 20))
        slot.points_override, slot.time_limit_override_sec = 2000, 5
        self.assertEqual((slot.points, slot.time_limit_sec), (2000, 5))

    def test_slot_reveal_mode_falls_back_to_kwiz(self):
        kwiz = make_kwiz(reveal_mode=RevealMode.HIDDEN)
        first, second = kwiz.ordered_slots()
        second.reveal_mode = RevealMode.FULL
        second.save()
        self.assertEqual(first.effective_reveal_mode, "hidden")
        self.assertFalse(first.is_scored)
        self.assertTrue(second.is_scored)
        self.assertTrue(kwiz.has_scored_slots)
        second.reveal_mode = RevealMode.COUNTS
        second.save()
        self.assertFalse(kwiz.has_scored_slots)

    def test_remix_keeps_reveal_modes(self):
        kwiz = make_kwiz(reveal_mode=RevealMode.COUNTS)
        kwiz.slots.filter(position=1).update(reveal_mode=RevealMode.HIDDEN)
        copy = kwiz.remix()
        self.assertEqual(copy.reveal_mode, "counts")
        self.assertEqual([s.reveal_mode for s in copy.ordered_slots()], ["", "hidden"])

    def test_remix_deep_copies_into_new_owners_bank(self):
        author = User.objects.create_user("author", "a@example.com", "pw")
        remixer = User.objects.create_user("remixer", "r@example.com", "pw")
        original = make_kwiz(owner=author, visibility=Kwiz.Visibility.PUBLIC)

        copy = original.remix(owner=remixer)

        self.assertEqual(copy.remixed_from, original)
        self.assertEqual(copy.owner, remixer)
        self.assertNotEqual(copy.edit_token, original.edit_token)
        copied = [s.kwestion for s in copy.ordered_slots()]
        self.assertEqual([k.prompt for k in copied], ["Q0", "Q1"])
        for kwestion in copied:
            self.assertEqual(kwestion.owner, remixer)
            self.assertIsNotNone(kwestion.remixed_from)
            self.assertEqual(kwestion.choices.count(), 2)
        self.assertEqual(Kwestion.objects.filter(owner=author).count(), 2)


class KwizApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("prof", "prof@example.com", "pw")
        self.client.force_login(self.user)
        self.kwiz = make_kwiz(owner=self.user)
        self.url = f"/api/kwizzes/{self.kwiz.pk}"

    def put(self, body, client=None):
        return (client or self.client).put(
            self.url, json.dumps(body), content_type="application/json"
        )

    def test_reorder_edit_and_delete_in_one_save(self):
        data = payload_for(self.client.get(self.url).json())
        first, second = data["slots"]
        second["kwestion"]["prompt"] = "Renamed"
        second["kwestion"]["choices"].reverse()
        data["slots"] = [second]

        response = self.put(data)

        self.assertEqual(response.status_code, 200, response.content)
        slots = list(self.kwiz.ordered_slots())
        self.assertEqual([s.kwestion.prompt for s in slots], ["Renamed"])
        self.assertEqual(slots[0].id, second["id"])
        self.assertEqual(
            [c.body for c in slots[0].kwestion.choices.all()], ["wrong", "right"]
        )
        # The removed kwestion stays in the bank: it belongs to a user.
        self.assertTrue(Kwestion.objects.filter(pk=first["kwestion_id"]).exists())

    def test_saves_reveal_modes(self):
        data = payload_for(self.client.get(self.url).json())
        data["reveal_mode"] = "hidden"
        data["slots"][1]["reveal_mode"] = "counts"
        self.assertEqual(self.put(data).status_code, 200)
        self.kwiz.refresh_from_db()
        self.assertEqual(self.kwiz.reveal_mode, "hidden")
        self.assertEqual(
            [s.reveal_mode for s in self.kwiz.ordered_slots()], ["", "counts"]
        )
        data["slots"][0]["reveal_mode"] = "secret"
        self.assertEqual(self.put(data).status_code, 422)

    def test_new_kwestions_go_into_the_bank(self):
        data = payload_for(self.client.get(self.url).json())
        data["slots"].append(
            {
                "kwestion": {
                    "prompt": "New",
                    "choices": [{"body": "yes", "correctness": 1}],
                }
            }
        )
        self.assertEqual(self.put(data).status_code, 200)
        self.assertTrue(Kwestion.objects.filter(owner=self.user, prompt="New").exists())

    def test_binary_kwestions_reject_partial_credit(self):
        data = payload_for(self.client.get(self.url).json())
        data["slots"][0]["kwestion"]["choices"][1]["correctness"] = 0.5
        response = self.put(data)
        self.assertEqual(response.status_code, 422)
        data["slots"][0]["kwestion"]["scoring_mode"] = "scale"
        self.assertEqual(self.put(data).status_code, 200)

    def test_public_kwiz_must_be_playable(self):
        data = payload_for(self.client.get(self.url).json())
        data["visibility"] = "public"
        data["slots"] = []
        response = self.put(data)
        self.assertEqual(response.status_code, 422)
        self.kwiz.refresh_from_db()
        self.assertEqual(self.kwiz.visibility, "private")
        self.assertEqual(self.kwiz.slots.count(), 2)

    def test_cannot_edit_someone_elses_kwestion(self):
        other = make_kwiz(owner=User.objects.create_user("x", "x@example.com", "pw"))
        foreign = other.slots.first().kwestion
        data = payload_for(self.client.get(self.url).json())
        data["slots"][0]["kwestion_id"] = foreign.pk
        data["slots"][0]["id"] = None
        self.assertEqual(self.put(data).status_code, 403)

    def test_other_users_cannot_see_or_edit_private_kwiz(self):
        stranger = Client()
        stranger.force_login(User.objects.create_user("s", "s@example.com", "pw"))
        self.assertEqual(stranger.get(self.url).status_code, 404)
        data = payload_for(self.client.get(self.url).json())
        self.assertEqual(self.put(data, client=stranger).status_code, 403)

    def test_writes_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        data = payload_for(self.client.get(self.url).json())
        self.assertEqual(self.put(data, client=client).status_code, 403)

    def test_bank_lists_only_my_kwestions(self):
        make_kwiz(owner=User.objects.create_user("y", "y@example.com", "pw"))
        prompts = [k["prompt"] for k in self.client.get("/api/kwestions/bank").json()]
        self.assertCountEqual(prompts, ["Q0", "Q1"])


class AnonymousCreatorTests(TestCase):
    def test_anonymous_create_edit_then_claim_on_signup(self):
        response = self.client.post(reverse("kwizzes:create"), {"title": "Mine"})
        kwiz = Kwiz.objects.get(title="Mine")
        self.assertRedirects(response, reverse("kwizzes:edit", args=[kwiz.pk]))
        self.assertIsNone(kwiz.owner)

        data = payload_for(self.client.get(f"/api/kwizzes/{kwiz.pk}").json())
        data["slots"] = [
            {"kwestion": {"prompt": "Q", "choices": [{"body": "a", "correctness": 1}]}}
        ]
        response = self.client.put(
            f"/api/kwizzes/{kwiz.pk}", json.dumps(data), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)

        game = GameSession.start_for(kwiz)
        player = Player.objects.create(game_session=game, nickname="me")
        session = self.client.session
        session[HOST_TOKENS] = [game.host_token]
        session[PLAYER_TOKENS] = [player.session_token]
        session.save()

        self.client.post(
            reverse("account_signup"),
            {
                "username": "newbie",
                "email": "newbie@example.com",
                "password1": "a-long-passphrase-42",
                "password2": "a-long-passphrase-42",
            },
        )
        user = User.objects.get(username="newbie")
        kwiz.refresh_from_db()
        game.refresh_from_db()
        player.refresh_from_db()
        self.assertEqual(kwiz.owner, user)
        self.assertEqual(Kwestion.objects.get(prompt="Q").owner, user)
        self.assertEqual(game.host_user, user)
        self.assertEqual(player.user, user)

    def test_secret_link_grants_edit_access(self):
        kwiz = make_kwiz()
        self.assertEqual(
            self.client.get(reverse("kwizzes:edit", args=[kwiz.pk])).status_code, 404
        )
        self.client.get(reverse("kwizzes:edit_by_token", args=[kwiz.edit_token]))
        self.assertIn(kwiz.edit_token, self.client.session[KWIZ_TOKENS])
        self.assertEqual(
            self.client.get(reverse("kwizzes:edit", args=[kwiz.pk])).status_code, 200
        )

    def test_pages_render(self):
        kwiz = make_kwiz(visibility=Kwiz.Visibility.PUBLIC)
        for url in (
            reverse("home"),
            reverse("kwizzes:library"),
            reverse("kwizzes:dashboard"),
            reverse("kwizzes:create"),
            reverse("kwizzes:detail", args=[kwiz.pk]),
            reverse("join"),
            reverse("account_login"),
            reverse("account_signup"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_anyone_can_remix_a_public_kwiz(self):
        kwiz = make_kwiz(visibility=Kwiz.Visibility.PUBLIC)
        response = self.client.post(reverse("kwizzes:remix", args=[kwiz.pk]))
        copy = Kwiz.objects.get(remixed_from=kwiz)
        self.assertRedirects(response, reverse("kwizzes:edit", args=[copy.pk]))

    def test_link_previews(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, '<meta property="og:title" content="KahDuke">')
        self.assertContains(
            response,
            '<meta property="og:image" content="http://testserver/static/img/og-card.png">',
        )
        kwiz = make_kwiz(visibility=Kwiz.Visibility.PUBLIC)
        kwiz.description = "Line one\nwith <b>tags</b> & such"
        kwiz.save()
        response = self.client.get(reverse("kwizzes:detail", args=[kwiz.pk]))
        self.assertContains(response, '<meta property="og:title" content="Sorting">')
        self.assertContains(
            response,
            '<meta property="og:description" content="Line one\nwith tags &amp; such">',
        )

    def test_footer_links_to_the_repo(self):
        repo = "https://github.com/hcientist/kahduke"
        self.assertContains(self.client.get(reverse("home")), repo)
        kwiz = make_kwiz(visibility=Kwiz.Visibility.PUBLIC)
        self.client.post(reverse("games:start", args=[kwiz.pk]))
        game = kwiz.games.get()
        # Not on the projector.
        response = self.client.get(reverse("games:host", args=[game.shortcode]))
        self.assertNotContains(response, repo)
