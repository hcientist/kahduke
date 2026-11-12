"""Kwizzes as Markdown: parse a .md file into a KwizIn, and render a kwiz back.

The format, all of it optional except at least a title:

    ---
    visibility: public          # private (default) | public
    reveal: full                # full (default) | counts | hidden
    ---

    # Sorting warm-up

    Any paragraphs here become the description.

    ## Which sort is stable?
    More lines of prompt, if one line isn't enough.
    ![diagram](https://example.com/sorts.png)
    {points=2000 time=30 reveal=counts}

    - [ ] Quicksort
    - [x] Merge sort
    - [0.5] Heapsort, if you squint ![](https://example.com/heap.png)
    - [ ] Selection sort {points=100}

Each `## ` heading starts a kwestion. `[x]` is right and `[ ]` wrong; a number
between 0 and 1 gives partial credit and switches the kwestion to scale
scoring (`scoring=scale` does too, explicitly). The `{...}` line sets the
kwestion's points, seconds to answer, scoring and reveal mode; on a choice it
sets that choice's own points. An image on its own line in a kwestion, or at
the end of a choice, becomes that item's media link.

The frontmatter is a flat `key: value` list rather than YAML, so there is no
YAML dependency. `title:` and `description:` work there too, if you'd rather
not use the `# ` heading.
"""

import re

from pydantic import ValidationError

from .schemas import KwestionIn, KwizIn

DEFAULT_POINTS = KwestionIn.model_fields["default_points"].default
DEFAULT_TIME = KwestionIn.model_fields["default_time_limit_sec"].default

ATTRS = re.compile(r"\{([^{}]*)\}\s*$")
IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(\S+?)\s*\)\s*$")
CHOICE = re.compile(r"^\s*[-*+]\s+\[\s*([xX]|[01](?:\.\d+)?|\.\d+)?\s*\]\s+(.*)$")
KWIZ_KEYS = {"title", "description", "visibility", "reveal"}
KWESTION_KEYS = {"points", "time", "scoring", "reveal"}
CHOICE_KEYS = {"points"}


class MarkdownError(ValueError):
    """The text isn't a valid kwiz. `errors` holds one message per problem."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("\n".join(errors))


def _attrs(text, allowed, where, errors):
    attrs = {}
    for part in text.split():
        key, sep, value = part.partition("=")
        key = key.strip().lower()
        if not sep or not value:
            errors.append(f"{where}: “{part}” should look like key=value.")
        elif key not in allowed:
            errors.append(
                f"{where}: unknown setting “{key}”. Use {', '.join(sorted(allowed))}."
            )
        else:
            attrs[key] = value.strip()
    return attrs


def _int(attrs, key, where, errors):
    if key not in attrs:
        return None
    try:
        return int(attrs[key])
    except ValueError:
        errors.append(f"{where}: {key} must be a whole number, not “{attrs[key]}”.")
        return None


def _strip_trailing(text, pattern):
    """Pull a trailing `pattern` match off `text`; returns (rest, group1)."""
    if match := pattern.search(text):
        return text[: match.start()].rstrip(), match.group(1)
    return text, None


def _frontmatter(lines, errors):
    """Consume a leading `---` block. Returns (settings, first body line)."""
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].strip() != "---":
        return {}, 0
    settings = {}
    for j in range(i + 1, len(lines)):
        line = lines[j].split(" #", 1)[0].strip()
        if line == "---":
            return settings, j + 1
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if not sep:
            errors.append(f"Line {j + 1}: frontmatter lines look like key: value.")
        elif key not in KWIZ_KEYS:
            errors.append(
                f"Line {j + 1}: unknown setting “{key}”. "
                f"Use {', '.join(sorted(KWIZ_KEYS))}."
            )
        else:
            settings[key] = value.strip().strip("\"'")
    errors.append(f"Line {i + 1}: the frontmatter never ends; close it with ---.")
    return settings, len(lines)


def _new_kwestion(heading, lineno):
    return {
        "line": lineno,
        "prompt": [heading] if heading else [],
        "media_url": "",
        "attrs": {},
        "choices": [],
    }


def _parse_choice(match, where, errors):
    mark, body = match.group(1), match.group(2).strip()
    body, attr_text = _strip_trailing(body, ATTRS)
    attrs = _attrs(attr_text, CHOICE_KEYS, where, errors) if attr_text else {}
    body, media = _strip_trailing(body, IMAGE)
    if not mark:
        correctness = 0.0
    elif mark in "xX":
        correctness = 1.0
    else:
        correctness = float(mark)
        if correctness > 1:
            errors.append(f"{where}: credit must be between 0 and 1, not {mark}.")
            correctness = 1.0
    return {
        "body": body,
        "media_url": media or "",
        "correctness": correctness,
        "points": _int(attrs, "points", where, errors),
    }


def _build_kwestion(raw, errors):
    where = f"Kwestion on line {raw['line']}"
    attrs = raw["attrs"]
    choices = raw["choices"]
    scoring = attrs.get("scoring", "").lower()
    partial = any(c["correctness"] not in (0, 1) for c in choices)
    if not scoring:
        scoring = "scale" if partial else "binary"
    elif scoring not in ("binary", "scale"):
        errors.append(f"{where}: scoring is binary or scale, not “{scoring}”.")
        scoring = "scale"
    elif scoring == "binary" and partial:
        errors.append(f"{where}: partial credit needs scoring=scale.")
        scoring = "scale"
    reveal = attrs.get("reveal", "").lower()
    if reveal not in ("", "full", "counts", "hidden"):
        errors.append(f"{where}: reveal is full, counts or hidden, not “{reveal}”.")
        reveal = ""
    if not choices:
        errors.append(f"{where}: add at least one “- [ ] answer” line.")
    points = _int(attrs, "points", where, errors)
    time = _int(attrs, "time", where, errors)
    return {
        "reveal_mode": reveal,
        "kwestion": {
            "prompt": "\n".join(raw["prompt"]).strip(),
            "media_url": raw["media_url"],
            "scoring_mode": scoring,
            "default_points": DEFAULT_POINTS if points is None else points,
            "default_time_limit_sec": DEFAULT_TIME if time is None else time,
            "choices": choices,
        },
    }


def _pydantic_errors(exc, data):
    messages = []
    for err in exc.errors():
        loc = list(err["loc"])
        where = "Kwiz"
        if len(loc) >= 2 and loc[0] == "slots":
            where = f"Kwestion {loc[1] + 1}"
            if len(loc) >= 5 and loc[3] == "choices":
                where += f", choice {loc[4] + 1}"
        field = loc[-1] if loc and isinstance(loc[-1], str) else ""
        msg = err["msg"].removeprefix("Value error, ")
        messages.append(f"{where}{f' ({field})' if field else ''}: {msg}")
    return messages


def parse(text):
    """Turn Markdown into a validated KwizIn. Raises MarkdownError."""
    errors = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    settings, start = _frontmatter(lines, errors)

    title = settings.get("title", "")
    description = []
    kwestions = []
    current = None
    in_fence = False

    for index in range(start, len(lines)):
        lineno = index + 1
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("```"):
            in_fence = not in_fence
        if in_fence or stripped.startswith("```"):
            # Code blocks are kept verbatim in a prompt or description.
            target = current["prompt"] if current else description
            target.append(line.rstrip())
            continue

        if stripped.startswith("## "):
            current = _new_kwestion(stripped[3:].strip(), lineno)
            kwestions.append(current)
            continue
        if stripped.startswith("# ") and current is None and not title:
            title = stripped[2:].strip()
            continue

        if current is None:
            description.append(line.rstrip())
            continue

        if match := CHOICE.match(line):
            current["choices"].append(_parse_choice(match, f"Line {lineno}", errors))
            continue
        if current["choices"] and stripped:
            if line[:1].isspace():
                # An indented line continues the previous choice.
                current["choices"][-1]["body"] += " " + stripped
                continue
            errors.append(
                f"Line {lineno}: text after the answer choices. Put prompt "
                "text above the choices, or start a new kwestion with ## ."
            )
            continue
        if ATTRS.fullmatch(stripped):
            current["attrs"] |= _attrs(
                stripped[1:-1], KWESTION_KEYS, f"Line {lineno}", errors
            )
            continue
        if (image := IMAGE.match(stripped)) and not current["media_url"]:
            current["media_url"] = image.group(1)
            continue
        current["prompt"].append(line.rstrip())

    if in_fence:
        errors.append("A ``` code block is never closed.")
    if not title:
        errors.append("Give the kwiz a title with a “# Title” line.")
    if "description" in settings:
        description = [settings["description"], "", *description]

    slots = [_build_kwestion(raw, errors) for raw in kwestions]
    data = {
        "title": title,
        "description": "\n".join(description).strip(),
        "visibility": settings.get("visibility", "private").lower(),
        "reveal_mode": settings.get("reveal", "full").lower(),
        "slots": slots,
    }
    if errors:
        raise MarkdownError(errors)
    try:
        return KwizIn.model_validate(data)
    except ValidationError as exc:
        raise MarkdownError(_pydantic_errors(exc, data)) from exc


def match_existing(kwiz, data):
    """Point the parsed slots and choices at `kwiz`'s rows, by position.

    Re-importing a kwiz then updates it in place instead of replacing every
    kwestion, so earlier games keep their responses.
    """
    slots = list(kwiz.ordered_slots().prefetch_related("kwestion__choices"))
    for slot, slot_in in zip(slots, data.slots, strict=False):
        slot_in.id = slot.pk
        slot_in.kwestion_id = slot.kwestion_id
        for choice, choice_in in zip(
            slot.kwestion.choices.all(), slot_in.kwestion.choices, strict=False
        ):
            choice_in.id = choice.pk
    return data


def _mark(correctness):
    value = float(correctness)
    if value == 1:
        return "x"
    if value == 0:
        return " "
    return f"{value:g}"


def render(kwiz):
    """The kwiz as Markdown that `parse` reads back to the same kwiz."""
    out = [
        "---",
        f"visibility: {kwiz.visibility}",
        f"reveal: {kwiz.reveal_mode}",
        "---",
        "",
        f"# {kwiz.title}",
        "",
    ]
    if kwiz.description.strip():
        out += [kwiz.description.strip(), ""]
    for slot in kwiz.ordered_slots().prefetch_related("kwestion__choices"):
        kwestion = slot.kwestion
        first, _, rest = kwestion.prompt.strip().partition("\n")
        out.append(f"## {first.strip()}")
        if rest.strip():
            out.append(rest.strip())
        if kwestion.media_url:
            out.append(f"![]({kwestion.media_url})")
        attrs = []
        if slot.points != DEFAULT_POINTS:
            attrs.append(f"points={slot.points}")
        if slot.time_limit_sec != DEFAULT_TIME:
            attrs.append(f"time={slot.time_limit_sec}")
        if kwestion.scoring_mode == "scale":
            attrs.append("scoring=scale")
        if slot.reveal_mode:
            attrs.append(f"reveal={slot.reveal_mode}")
        if attrs:
            out.append("{" + " ".join(attrs) + "}")
        out.append("")
        for choice in kwestion.choices.all():
            line = f"- [{_mark(choice.correctness)}] {choice.body}"
            if choice.media_url:
                line += f" ![]({choice.media_url})"
            if choice.points is not None:
                line += f" {{points={choice.points}}}"
            out.append(line)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def filename(kwiz):
    title = re.sub(r"['’]", "", kwiz.title.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", title).strip("-")
    return f"{slug or 'kwiz'}.md"
