from datetime import datetime
from typing import Literal

from ninja import Field, Schema
from pydantic import model_validator


class ChoiceIn(Schema):
    id: int | None = None
    body: str = Field(min_length=1, max_length=500)
    media_url: str = ""
    correctness: float = Field(ge=0, le=1)
    points: int | None = Field(default=None, ge=0)


class KwestionIn(Schema):
    prompt: str = Field(min_length=1)
    media_url: str = ""
    scoring_mode: Literal["binary", "scale"] = "binary"
    default_points: int = Field(default=1000, ge=0, le=10000)
    default_time_limit_sec: int = Field(default=20, ge=5, le=240)
    choices: list[ChoiceIn] = Field(min_length=1)

    @model_validator(mode="after")
    def binary_means_all_or_nothing(self):
        if self.scoring_mode == "binary":
            for choice in self.choices:
                if choice.correctness not in (0, 1):
                    raise ValueError(
                        "Right-or-wrong kwestions need each choice to be fully "
                        "right (1) or wrong (0). Switch to partial credit to "
                        "use values in between."
                    )
        return self


RevealModeName = Literal["full", "counts", "hidden"]
PlayerScreenName = Literal["full", "answers", "shapes"]


class SlotIn(Schema):
    id: int | None = None
    kwestion_id: int | None = None
    points_override: int | None = Field(default=None, ge=0, le=10000)
    time_limit_override_sec: int | None = Field(default=None, ge=5, le=240)
    # "" means use the kwiz's reveal_mode.
    reveal_mode: RevealModeName | Literal[""] = ""
    kwestion: KwestionIn


class KwizIn(Schema):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    visibility: Literal["private", "public"] = "private"
    reveal_mode: RevealModeName = "full"
    player_screen: PlayerScreenName = "full"
    slots: list[SlotIn] = []


class ChoiceOut(Schema):
    id: int
    position: int
    body: str
    media_url: str
    correctness: float
    points: int | None


class KwestionOut(Schema):
    id: int
    prompt: str
    media_url: str
    scoring_mode: str
    default_points: int
    default_time_limit_sec: int
    choices: list[ChoiceOut]
    used_in: int = 0

    @staticmethod
    def resolve_choices(obj):
        return list(obj.choices.all())

    @staticmethod
    def resolve_used_in(obj):
        if (annotated := getattr(obj, "used_in", None)) is not None:
            return annotated
        return obj.slots.values("kwiz").distinct().count()


class SlotOut(Schema):
    id: int
    position: int
    kwestion_id: int
    points_override: int | None
    time_limit_override_sec: int | None
    reveal_mode: str
    kwestion: KwestionOut


class KwizOut(Schema):
    id: int
    title: str
    description: str
    visibility: str
    reveal_mode: str
    player_screen: str
    owner: str | None
    remixed_from_id: int | None
    updated_at: datetime
    can_edit: bool = False
    slots: list[SlotOut]

    @staticmethod
    def resolve_owner(obj):
        return str(obj.owner) if obj.owner else None

    @staticmethod
    def resolve_slots(obj):
        return list(obj.ordered_slots().prefetch_related("kwestion__choices"))


class KwizSummary(Schema):
    id: int
    title: str
    description: str
    owner: str | None
    kwestion_count: int
    updated_at: datetime

    @staticmethod
    def resolve_owner(obj):
        return str(obj.owner) if obj.owner else None


class RemixOut(Schema):
    id: int
    edit_url: str


class GameOut(Schema):
    shortcode: str
    kwiz_title: str
    status: str
    player_count: int
