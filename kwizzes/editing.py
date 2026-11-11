"""Saving a whole kwiz, as sent by the editor, in one transaction."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F

from .models import AnswerChoice, Kwestion, KwizKwestion

# Positions are unique per parent, so a reorder first moves every existing row
# out of the way, then writes the final positions.
POSITION_OFFSET = 1_000_000


def _owned_kwestion(kwiz, kwestion_id, user):
    kwestion = Kwestion.objects.filter(pk=kwestion_id).first()
    if kwestion is None:
        raise PermissionDenied(f"Kwestion {kwestion_id} does not exist.")
    in_this_kwiz = kwestion.slots.filter(kwiz=kwiz).exists()
    in_users_bank = user.is_authenticated and kwestion.owner_id == user.pk
    if not (in_this_kwiz or in_users_bank):
        raise PermissionDenied(f"You can't edit kwestion {kwestion_id}.")
    return kwestion


def _save_choices(kwestion, choices):
    existing = {c.pk: c for c in kwestion.choices.all()}
    kept_ids = {c.id for c in choices if c.id in existing}
    kwestion.choices.exclude(pk__in=kept_ids).delete()
    kwestion.choices.update(position=F("position") + POSITION_OFFSET)
    for position, data in enumerate(choices):
        choice = existing.get(data.id) or AnswerChoice(kwestion=kwestion)
        choice.position = position
        choice.body = data.body
        choice.media_url = data.media_url
        choice.correctness = round(data.correctness, 3)
        choice.points = data.points
        choice.save()


def _save_kwestion(kwiz, slot_data, user):
    data = slot_data.kwestion
    if slot_data.kwestion_id:
        kwestion = _owned_kwestion(kwiz, slot_data.kwestion_id, user)
    else:
        kwestion = Kwestion(owner=user if user.is_authenticated else None)
    kwestion.prompt = data.prompt
    kwestion.media_url = data.media_url
    kwestion.scoring_mode = data.scoring_mode
    kwestion.default_points = data.default_points
    kwestion.default_time_limit_sec = data.default_time_limit_sec
    kwestion.save()
    _save_choices(kwestion, data.choices)
    return kwestion


@transaction.atomic
def save_kwiz(kwiz, data, user):
    """Apply a KwizIn payload to `kwiz`.

    Slots and choices that carry an id are updated in place; ones without are
    created; ones missing from the payload are deleted. Editing a kwestion
    from your bank changes it in every kwiz that uses it.
    """
    kwiz.title = data.title
    kwiz.description = data.description
    kwiz.visibility = data.visibility
    kwiz.reveal_mode = data.reveal_mode
    kwiz.save()

    existing = {s.pk: s for s in kwiz.slots.all()}
    kept_ids = {s.id for s in data.slots if s.id in existing}
    removed = kwiz.slots.exclude(pk__in=kept_ids)
    removed_kwestion_ids = list(removed.values_list("kwestion_id", flat=True))
    removed.delete()
    kwiz.slots.update(position=F("position") + POSITION_OFFSET)

    for position, slot_data in enumerate(data.slots):
        kwestion = _save_kwestion(kwiz, slot_data, user)
        slot = existing.get(slot_data.id) or KwizKwestion(kwiz=kwiz)
        slot.kwestion = kwestion
        slot.position = position
        slot.points_override = slot_data.points_override
        slot.time_limit_override_sec = slot_data.time_limit_override_sec
        slot.reveal_mode = slot_data.reveal_mode
        slot.save()

    # Anonymous kwestions live only in their kwiz; once unused they are garbage.
    Kwestion.objects.filter(
        pk__in=removed_kwestion_ids, owner=None, slots=None
    ).delete()

    if kwiz.is_public and (errors := kwiz.playability_errors()):
        raise ValidationError(["A kwiz must be playable to be public."] + errors)
    return kwiz
