from django.core.management.base import BaseCommand
from django.db import transaction

from kwizzes.models import AnswerChoice, Kwestion, Kwiz, KwizKwestion

DEMO_TITLE = "CS 347 warm-up"

KWESTIONS = [
    {
        "prompt": "Which HTTP method should a form use to change data on the server?",
        "choices": [("GET", 0), ("POST", 1), ("HEAD", 0), ("OPTIONS", 0)],
    },
    {
        "prompt": "What does Django's ORM turn a QuerySet into?",
        "choices": [("HTML", 0), ("JSON", 0), ("SQL", 1), ("YAML", 0)],
        "time": 15,
    },
    {
        "prompt": "Where is the best place to keep SECRET_KEY for production?",
        "scale": True,
        "choices": [
            ("An environment variable", 1),
            ("A gitignored .env file", 0.75),
            ("settings.py, in a private repo", 0.25),
            ("settings.py, in a public repo", 0),
        ],
    },
    {
        "prompt": "Which protocol lets Channels push updates to a browser?",
        "choices": [("WebSocket", 1), ("FTP", 0), ("SMTP", 0), ("SSH", 0)],
        "points": 2000,
    },
]


class Command(BaseCommand):
    help = "Create a public demo kwiz, replacing an earlier copy of it."

    @transaction.atomic
    def handle(self, *args, **options):
        Kwiz.objects.filter(title=DEMO_TITLE, owner=None).delete()
        kwiz = Kwiz.objects.create(
            title=DEMO_TITLE,
            description="Four kwestions on the web stack this course builds on.",
            visibility=Kwiz.Visibility.PUBLIC,
        )
        for position, spec in enumerate(KWESTIONS):
            kwestion = Kwestion.objects.create(
                prompt=spec["prompt"],
                scoring_mode="scale" if spec.get("scale") else "binary",
                default_points=spec.get("points", 1000),
                default_time_limit_sec=spec.get("time", 20),
            )
            for i, (body, correctness) in enumerate(spec["choices"]):
                AnswerChoice.objects.create(
                    kwestion=kwestion, position=i, body=body, correctness=correctness
                )
            KwizKwestion.objects.create(kwiz=kwiz, kwestion=kwestion, position=position)
        self.stdout.write(self.style.SUCCESS(f"Created “{kwiz}” (id {kwiz.pk})."))
