"""Print what the accessibility scan needs to know about the app, as JSON.

    uv run python a11y/app_info.py > a11y/app.json

- "routes": every URL pattern Django serves, so scan.mjs can fail when a route
  is neither scanned nor listed in its EXCLUDE table.
- "demo_kwiz": the ID of the kwiz `manage.py seed_demo` creates, so the scan
  can open it without hard-coding its title. Run this against the same
  database the server uses, after seed_demo.
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kahduke_config.settings")

import django  # noqa: E402

django.setup()

from django.urls import URLPattern, URLResolver, get_resolver  # noqa: E402

from kwizzes.management.commands.seed_demo import DEMO_TITLE  # noqa: E402
from kwizzes.models import Kwiz  # noqa: E402


def js_regex(pattern):
    """A Python URL regex fragment, rewritten for JavaScript's RegExp."""
    pattern = pattern.removeprefix("^")
    pattern = re.sub(r"\\Z$|\$$", "", pattern)
    return pattern.replace("(?P<", "(?<")


def walk(patterns, route="", regex="", namespace=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            ns = namespace
            if p.namespace:
                ns = f"{namespace}{p.namespace}:"
            yield from walk(
                p.url_patterns,
                route + str(p.pattern),
                regex + js_regex(p.pattern.regex.pattern),
                ns,
            )
        elif isinstance(p, URLPattern):
            yield {
                "route": route + str(p.pattern),
                "name": f"{namespace}{p.name}" if p.name else None,
                "regex": f"^/{regex}{js_regex(p.pattern.regex.pattern)}$",
            }


# Remixes made without signing in also have no owner and the same title.
demo = Kwiz.objects.filter(
    title=DEMO_TITLE,
    owner=None,
    remixed_from=None,
    visibility=Kwiz.Visibility.PUBLIC,
).first()
json.dump(
    {
        "routes": list(walk(get_resolver().url_patterns)),
        "demo_kwiz": demo.pk if demo else None,
    },
    sys.stdout,
    indent=2,
)
print()
