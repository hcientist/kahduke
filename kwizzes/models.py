import secrets

from django.conf import settings
from django.db import models, transaction
from django.urls import reverse


def new_token():
    return secrets.token_urlsafe(24)


class RevealMode(models.TextChoices):
    """What the host and player screens show after a kwestion closes.

    Only FULL reveals which answers were right. COUNTS and HIDDEN never say
    whether anyone got it right, so their kwestions also add nothing to
    scores (a score jump would give it away).
    """

    FULL = "full", "Show the answer and how many picked each"
    COUNTS = "counts", "Show how many picked each, not the answer"
    HIDDEN = "hidden", "Show nothing"


class Kwiz(models.Model):
    class Visibility(models.TextChoices):
        PRIVATE = "private", "Private"
        PUBLIC = "public", "Public"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="kwizzes",
        help_text="Null for an anonymous creator, who edits via edit_token.",
    )
    edit_token = models.CharField(max_length=64, unique=True, default=new_token)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    visibility = models.CharField(
        max_length=10, choices=Visibility, default=Visibility.PRIVATE
    )
    reveal_mode = models.CharField(
        max_length=10,
        choices=RevealMode,
        default=RevealMode.FULL,
        help_text="Default for every kwestion; each slot can override it.",
    )
    remixed_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="remixes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "kwizzes"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("kwizzes:detail", args=[self.pk])

    @property
    def is_public(self):
        return self.visibility == self.Visibility.PUBLIC

    @property
    def has_scored_slots(self):
        """True if any kwestion reveals answers, so scores mean something."""
        if self.reveal_mode == RevealMode.FULL:
            return self.slots.exclude(
                reveal_mode__in=[RevealMode.COUNTS, RevealMode.HIDDEN]
            ).exists()
        return self.slots.filter(reveal_mode=RevealMode.FULL).exists()

    def ordered_slots(self):
        return self.slots.select_related("kwestion").order_by("position")

    def playability_errors(self):
        """Why this kwiz can't be hosted or published yet; empty when it can.

        The ERD's 1+ rules (a kwiz has 1+ kwestions, a kwestion has 1+
        answer choices) can't be database constraints, so they live here.
        """
        errors = []
        slots = list(self.ordered_slots().prefetch_related("kwestion__choices"))
        if not slots:
            errors.append("Add at least one kwestion.")
        for slot in slots:
            if not slot.kwestion.choices.all():
                errors.append(f"Kwestion {slot.position + 1} needs an answer choice.")
        return errors

    @transaction.atomic
    def remix(self, owner=None):
        """Deep-copy this kwiz, its kwestions and their choices for a new owner.

        Copied kwestions land in the new owner's bank, each pointing back at
        its source for attribution.
        """
        copy = Kwiz.objects.create(
            owner=owner,
            title=self.title,
            description=self.description,
            reveal_mode=self.reveal_mode,
            remixed_from=self,
        )
        for slot in self.ordered_slots().prefetch_related("kwestion__choices"):
            kwestion = slot.kwestion.copy(owner=owner)
            KwizKwestion.objects.create(
                kwiz=copy,
                kwestion=kwestion,
                position=slot.position,
                points_override=slot.points_override,
                time_limit_override_sec=slot.time_limit_override_sec,
                reveal_mode=slot.reveal_mode,
            )
        return copy


class Kwestion(models.Model):
    class ScoringMode(models.TextChoices):
        BINARY = "binary", "Right or wrong"
        SCALE = "scale", "Partial credit"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="kwestion_bank",
    )
    remixed_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="remixes",
    )
    prompt = models.TextField()
    media_url = models.URLField(blank=True)
    scoring_mode = models.CharField(
        max_length=10, choices=ScoringMode, default=ScoringMode.BINARY
    )
    default_points = models.PositiveIntegerField(default=1000)
    default_time_limit_sec = models.PositiveIntegerField(default=20)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.prompt[:60]

    def copy(self, owner=None):
        copy = Kwestion.objects.create(
            owner=owner,
            remixed_from=self,
            prompt=self.prompt,
            media_url=self.media_url,
            scoring_mode=self.scoring_mode,
            default_points=self.default_points,
            default_time_limit_sec=self.default_time_limit_sec,
        )
        AnswerChoice.objects.bulk_create(
            AnswerChoice(
                kwestion=copy,
                position=choice.position,
                body=choice.body,
                media_url=choice.media_url,
                correctness=choice.correctness,
                points=choice.points,
            )
            for choice in self.choices.all()
        )
        return copy


class KwizKwestion(models.Model):
    """One ordered slot in a kwiz. The same bank kwestion can fill slots in
    many kwizzes, each with its own points and time limit."""

    kwiz = models.ForeignKey(Kwiz, on_delete=models.CASCADE, related_name="slots")
    kwestion = models.ForeignKey(
        Kwestion, on_delete=models.CASCADE, related_name="slots"
    )
    position = models.PositiveIntegerField()
    points_override = models.PositiveIntegerField(null=True, blank=True)
    time_limit_override_sec = models.PositiveIntegerField(null=True, blank=True)
    # Blank means "use the kwiz's reveal_mode".
    reveal_mode = models.CharField(
        max_length=10, choices=RevealMode, blank=True, default=""
    )

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["kwiz", "position"], name="unique_kwestion_position_per_kwiz"
            ),
        ]

    def __str__(self):
        return f"{self.kwiz} #{self.position + 1}: {self.kwestion}"

    @property
    def points(self):
        if self.points_override is not None:
            return self.points_override
        return self.kwestion.default_points

    @property
    def time_limit_sec(self):
        if self.time_limit_override_sec is not None:
            return self.time_limit_override_sec
        return self.kwestion.default_time_limit_sec

    @property
    def effective_reveal_mode(self):
        return self.reveal_mode or self.kwiz.reveal_mode

    @property
    def is_scored(self):
        return self.effective_reveal_mode == RevealMode.FULL


class AnswerChoice(models.Model):
    kwestion = models.ForeignKey(
        Kwestion, on_delete=models.CASCADE, related_name="choices"
    )
    position = models.PositiveIntegerField()
    body = models.CharField(max_length=500)
    media_url = models.URLField(blank=True)
    correctness = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        default=0,
        help_text="0 to 1. Binary kwestions use only 0 or 1.",
    )
    points = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Overrides correctness × kwestion points when set.",
    )

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["kwestion", "position"],
                name="unique_choice_position_per_kwestion",
            ),
            models.CheckConstraint(
                condition=models.Q(correctness__gte=0) & models.Q(correctness__lte=1),
                name="correctness_between_0_and_1",
            ),
        ]

    def __str__(self):
        return self.body

    @property
    def is_correct(self):
        return self.correctness > 0
