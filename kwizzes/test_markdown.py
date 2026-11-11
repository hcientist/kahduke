from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from games.models import GameSession, Player, Response

from . import markdown
from .access import KWIZ_TOKENS
from .editing import save_kwiz
from .models import Kwiz

User = get_user_model()

SAMPLE = """---
visibility: public   # anyone can host it
reveal: counts
---

# Sorting warm-up

A few kwestions on sorting.

Second paragraph.

## Which sort is stable?
Think about equal keys.
![diagram](https://example.com/sorts.png)
{points=2000 time=30 reveal=full}

- [ ] Quicksort
- [x] Merge sort
- [ ] Selection sort {points=100}

## How fast is heapsort?
{scoring=scale}

- [1] O(n log n)
- [0.5] O(n^2) ![](https://example.com/n2.png)
- [ ] O(1)
"""


class ParseTests(TestCase):
    def test_full_sample(self):
        data = markdown.parse(SAMPLE)
        self.assertEqual(data.title, "Sorting warm-up")
        self.assertEqual(
            data.description, "A few kwestions on sorting.\n\nSecond paragraph."
        )
        self.assertEqual(data.visibility, "public")
        self.assertEqual(data.reveal_mode, "counts")
        first, second = data.slots
        self.assertEqual(first.reveal_mode, "full")
        self.assertEqual(
            first.kwestion.prompt, "Which sort is stable?\nThink about equal keys."
        )
        self.assertEqual(first.kwestion.media_url, "https://example.com/sorts.png")
        self.assertEqual(first.kwestion.default_points, 2000)
        self.assertEqual(first.kwestion.default_time_limit_sec, 30)
        self.assertEqual(
            [(c.body, c.correctness, c.points) for c in first.kwestion.choices],
            [
                ("Quicksort", 0, None),
                ("Merge sort", 1, None),
                ("Selection sort", 0, 100),
            ],
        )
        self.assertEqual(second.kwestion.scoring_mode, "scale")
        self.assertEqual(second.kwestion.choices[1].correctness, 0.5)
        self.assertEqual(second.kwestion.choices[1].body, "O(n^2)")
        self.assertEqual(
            second.kwestion.choices[1].media_url, "https://example.com/n2.png"
        )

    def test_partial_credit_implies_scale(self):
        data = markdown.parse("# T\n## Q\n- [0.25] a\n- [x] b\n")
        self.assertEqual(data.slots[0].kwestion.scoring_mode, "scale")

    def test_errors_name_the_line(self):
        with self.assertRaises(markdown.MarkdownError) as ctx:
            markdown.parse(
                "# T\n## Q\n{points=lots colour=red}\n- [x] a\nstray\n## Empty\n"
            )
        errors = "\n".join(ctx.exception.errors)
        self.assertIn("Kwestion on line 2: points must be a whole number", errors)
        self.assertIn("Line 3: unknown setting “colour”", errors)
        self.assertIn("Line 5: text after the answer choices", errors)
        self.assertIn("Kwestion on line 6: add at least one", errors)

    def test_missing_title(self):
        with self.assertRaises(markdown.MarkdownError):
            markdown.parse("## Q\n- [x] a\n")

    def test_schema_limits_are_reported(self):
        with self.assertRaises(markdown.MarkdownError) as ctx:
            markdown.parse("# T\n## Q\n{time=1}\n- [x] a\n")
        self.assertIn("Kwestion 1 (default_time_limit_sec)", ctx.exception.errors[0])

    def test_round_trip(self):
        kwiz = save_kwiz(Kwiz(), markdown.parse(SAMPLE), AnonymousUser())
        text = markdown.render(kwiz)
        self.assertEqual(markdown.parse(text), markdown.parse(SAMPLE))
        self.assertEqual(markdown.render(kwiz), text)


class ViewTests(TestCase):
    def test_import_creates_an_anonymous_kwiz(self):
        response = self.client.post(reverse("kwizzes:import"), {"markdown": SAMPLE})
        kwiz = Kwiz.objects.get()
        self.assertRedirects(response, kwiz.get_absolute_url())
        self.assertIsNone(kwiz.owner)
        self.assertIn(kwiz.edit_token, self.client.session[KWIZ_TOKENS])
        self.assertEqual(kwiz.slots.count(), 2)

    def test_upload_and_owner(self):
        user = User.objects.create_user(username="ada", password="pw")
        self.client.force_login(user)
        upload = SimpleUploadedFile(
            "k.md", SAMPLE.encode(), content_type="text/markdown"
        )
        self.client.post(reverse("kwizzes:import"), {"file": upload})
        self.assertEqual(Kwiz.objects.get().owner, user)

    def test_bad_markdown_shows_errors(self):
        response = self.client.post(reverse("kwizzes:import"), {"markdown": "## Q\n"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "title")
        self.assertFalse(Kwiz.objects.exists())

    def test_export_needs_view_access(self):
        kwiz = save_kwiz(
            Kwiz(),
            markdown.parse(SAMPLE.replace("public", "private", 1)),
            AnonymousUser(),
        )
        url = reverse("kwizzes:export", args=[kwiz.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        kwiz.visibility = "public"
        kwiz.save()
        response = self.client.get(url + "?download")
        self.assertEqual(
            response["Content-Disposition"], 'attachment; filename="sorting-warm-up.md"'
        )
        self.assertContains(response, "## Which sort is stable?")

    def test_reimport_keeps_responses(self):
        self.client.post(reverse("kwizzes:import"), {"markdown": SAMPLE})
        kwiz = Kwiz.objects.get()
        slot = kwiz.ordered_slots().first()
        game = GameSession.objects.create(kwiz=kwiz)
        player = Player.objects.create(game_session=game, nickname="p")
        Response.objects.create(
            player=player,
            kwiz_kwestion=slot,
            answer_choice=slot.kwestion.choices.get(position=1),
            response_ms=100,
            points_awarded=1500,
        )
        edited = SAMPLE.replace("Merge sort", "Merge sort (stable!)")
        url = reverse("kwizzes:reimport", args=[kwiz.pk])
        self.assertContains(self.client.get(url), "## Which sort is stable?")
        self.client.post(url, {"markdown": edited})
        self.assertEqual(Response.objects.count(), 1)
        slot.refresh_from_db()
        self.assertEqual(
            slot.kwestion.choices.get(position=1).body, "Merge sort (stable!)"
        )

    def test_reimport_needs_edit_access(self):
        kwiz = save_kwiz(Kwiz(), markdown.parse(SAMPLE), AnonymousUser())
        response = self.client.post(
            reverse("kwizzes:reimport", args=[kwiz.pk]), {"markdown": SAMPLE}
        )
        self.assertEqual(response.status_code, 404)
