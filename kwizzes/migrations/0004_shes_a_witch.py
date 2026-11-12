"""Seed a second public demo kwiz: the witch scene from Monty Python and the
Holy Grail, following Sir Bedevere's "strict logical reasoning" one step at a
time, and ending with a question on where that reasoning goes wrong.

The scene's transcript is on Mathematics Goes to the Movies:
https://www.qedcat.com/moviemath/holy_grail.html

The same kwiz, as Markdown, is examples/shes-a-witch.md; a test keeps the
two in step. Self-contained on purpose: migrations shouldn't import each
other or app code.
"""

from django.db import migrations

TITLE = "She's a Witch!"
DESCRIPTION = (
    "A village brings Sir Bedevere a witch. Bedevere, wise in the ways of "
    "science, settles it by strict logical reasoning. Follow along, one "
    "step at a time.\n\n"
    "Scene transcript: https://www.qedcat.com/moviemath/holy_grail.html"
)

# (prompt, settings, [(body, correctness, points or None)])
KWESTIONS = [
    (
        "How do you know she is a witch?",
        {"reveal_mode": "counts", "time": 15},
        [
            ("She looks like one", 1, None),
            ("She's got a wart", 1, None),
            ("She turned me into a newt! (I got better.)", 1, None),
            ("They dressed me up like this!", 1, None),
        ],
    ),
    (
        "What do you do with witches?",
        {"time": 10},
        [
            ("Burn them!", 1, None),
            ("Build a bridge out of them", 0, None),
            ("Throw them into the pond", 0, None),
            ("Ask them to kindly stop", 0, None),
        ],
    ),
    (
        "And what do you burn, apart from witches?",
        {"scoring_mode": "scale"},
        [
            ("Wood", 1, None),
            ("More witches!", 0.25, None),
            ("Shrubberies", 0, None),
            ("Coconuts", 0, None),
        ],
    ),
    (
        "So, why do witches burn?",
        {},
        [
            ("Because they're made of… wood?", 1, None),
            ("Because they're witches", 0, None),
            ("Because of the newts", 0, None),
        ],
    ),
    (
        "How do we tell whether she is made of wood?",
        {"scoring_mode": "scale"},
        [
            ("See whether she floats in water", 1, None),
            ("Build a bridge out of her (but bridges are also made of stone)", 0.25, None),
            ("Throw her into the pond and see if she sinks", 0.5, None),
            ("Ask her", 0, None),
        ],
    ),
    (
        "What also floats in water?",
        {"points": 1500},
        [
            ("A duck!", 1, None),
            ("Very small rocks", 0, None),
            ("Churches", 0, None),
            ("Lead", 0, None),
        ],
    ),
    (
        "So, logically, if she weighs the same as a duck…",
        {},
        [
            ("…she's made of wood, and therefore a witch!", 1, None),
            ("…she's a duck", 0, None),
            ("…the scales are broken", 0, None),
            ("…we need a bigger duck", 0, None),
        ],
    ),
    (
        "Bedevere argues: wood floats, and things that weigh the same as a duck "
        "float, so she is wood. Which fallacy is that?",
        {"points": 2000, "time": 40},
        [
            ("Affirming the consequent", 1, None),
            ("Undistributed middle", 1, None),
            ("Ad hominem", 0, None),
            ("None: it's sound. Burn her!", 0, None),
        ],
    ),
    (
        "Who are you, who are so wise in the ways of science?",
        {"points": 500, "time": 10},
        [
            ("Arthur, King of the Britons", 1, None),
            ("Sir Bedevere the Wise", 0, None),
            ("Tim the Enchanter", 0, None),
            ("A newt", 0, None),
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
    dependencies = [("kwizzes", "0003_bridge_of_death")]

    operations = [migrations.RunPython(add_demo, remove_demo)]
