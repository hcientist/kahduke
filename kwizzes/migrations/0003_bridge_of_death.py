"""Seed a public demo kwiz: the Bridgekeeper's questions from Monty Python
and the Holy Grail.

It shows off most of what a kwiz can do: a counts-only kwestion, partial
credit, a choice with its own points, longer time limits and a bigger prize.
The same kwiz, as Markdown, is examples/bridge-of-death.md; a test keeps the
two in step.

Unapplying removes it, but only if it's still unowned (nobody has claimed
it through the admin).
"""

from django.db import migrations

TITLE = "The Bridge of Death"
DESCRIPTION = (
    "Stop! Who would cross the Bridge of Death must answer me these "
    "questions three, ere the other side he see.\n\n"
    "(There are six. The Bridgekeeper was never good at counting.)"
)

# (prompt, settings, [(body, correctness, points or None)])
KWESTIONS = [
    (
        "What… is your name?",
        {"reveal_mode": "counts"},
        [
            ("Arthur, King of the Britons", 1, None),
            ("Sir Lancelot of Camelot", 1, None),
            ("Sir Robin the Not-Quite-So-Brave-as-Sir-Lancelot", 1, None),
            ("Sir Galahad the Pure", 1, None),
        ],
    ),
    (
        "What… is your quest?",
        {},
        [
            ("To seek the Holy Grail", 1, None),
            ("To find a shrubbery", 0, None),
            ("To say “Ni!”", 0, None),
            ("Run away! Run away!", 0, None),
        ],
    ),
    (
        "What… is your favourite colour?",
        {"scoring_mode": "scale", "time": 10},
        [
            ("Blue", 1, None),
            ("Yellow", 0.5, None),
            ("Blue. No, yel— AAAAAAAAH!", 0, None),
        ],
    ),
    (
        "What… is the capital of Assyria?",
        {"scoring_mode": "scale"},
        [
            ("Assur", 1, None),
            ("Nineveh", 0.75, None),
            ("Nimrud", 0.5, None),
            ("I don't know that!", 0, None),
        ],
    ),
    (
        "What… is the number of the counting, with the Holy Hand Grenade of Antioch?",
        {"scoring_mode": "scale"},
        [
            ("Three", 1, None),
            ("Two, excepting that thou then proceed to three", 0.5, None),
            ("Four", 0, None),
            ("Five", 0, None),
        ],
    ),
    (
        "What… is the airspeed velocity of an unladen swallow?",
        {"points": 2000, "time": 30},
        [
            ("What do you mean? An African or a European swallow?", 1, 3000),
            ("About 11 metres per second (24 mph)", 1, None),
            ("I don't know that!", 0, None),
            ("A swallow carrying a coconut", 0, None),
        ],
    ),
]


def add_demo(apps, schema_editor):
    Kwiz = apps.get_model("kwizzes", "Kwiz")
    Kwestion = apps.get_model("kwizzes", "Kwestion")
    KwizKwestion = apps.get_model("kwizzes", "KwizKwestion")
    AnswerChoice = apps.get_model("kwizzes", "AnswerChoice")

    if Kwiz.objects.filter(title=TITLE, owner=None).exists():
        return
    kwiz = Kwiz.objects.create(
        title=TITLE,
        description=DESCRIPTION,
        visibility="public",
        reveal_mode="full",
    )
    for position, (prompt, options, choices) in enumerate(KWESTIONS):
        kwestion = Kwestion.objects.create(
            prompt=prompt,
            scoring_mode=options.get("scoring_mode", "binary"),
            default_points=options.get("points", 1000),
            default_time_limit_sec=options.get("time", 20),
        )
        AnswerChoice.objects.bulk_create(
            AnswerChoice(
                kwestion=kwestion,
                position=i,
                body=body,
                correctness=correctness,
                points=points,
            )
            for i, (body, correctness, points) in enumerate(choices)
        )
        KwizKwestion.objects.create(
            kwiz=kwiz,
            kwestion=kwestion,
            position=position,
            reveal_mode=options.get("reveal_mode", ""),
        )


def remove_demo(apps, schema_editor):
    Kwiz = apps.get_model("kwizzes", "Kwiz")
    Kwestion = apps.get_model("kwizzes", "Kwestion")
    for kwiz in Kwiz.objects.filter(title=TITLE, owner=None):
        kwestion_ids = list(kwiz.slots.values_list("kwestion_id", flat=True))
        kwiz.delete()
        Kwestion.objects.filter(pk__in=kwestion_ids, owner=None, slots=None).delete()


class Migration(migrations.Migration):
    dependencies = [("kwizzes", "0002_reveal_mode")]

    operations = [migrations.RunPython(add_demo, remove_demo)]
