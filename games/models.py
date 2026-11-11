import secrets
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.urls import reverse
from django.utils import timezone

from kwizzes.models import AnswerChoice, Kwiz, KwizKwestion, new_token

SHORTCODE_LENGTH = 6
# Answers that arrive this long after the timer ends still count, so a slow
# phone on campus Wi-Fi isn't punished for network lag.
ANSWER_GRACE_MS = 1500


def new_shortcode():
    return "".join(secrets.choice("0123456789") for _ in range(SHORTCODE_LENGTH))


def score(points_base, correctness, choice_points, time_limit_ms, response_ms):
    """Kahoot-style scoring.

    A choice is worth `correctness × kwestion points`, or its own `points`
    when set. An instant answer earns the full amount; one at the buzzer
    earns half.
    """
    if choice_points is not None:
        base = Decimal(choice_points)
    else:
        base = Decimal(points_base) * Decimal(correctness)
    if base <= 0:
        return 0
    elapsed = min(max(response_ms, 0), time_limit_ms) / time_limit_ms
    return round(base * Decimal(1 - elapsed / 2))


class GameSessionQuerySet(models.QuerySet):
    def active(self):
        return self.exclude(status=GameSession.Status.FINISHED)


class GameSession(models.Model):
    class Status(models.TextChoices):
        LOBBY = "lobby", "Lobby"
        IN_PROGRESS = "in_progress", "In progress"
        FINISHED = "finished", "Finished"

    kwiz = models.ForeignKey(Kwiz, on_delete=models.CASCADE, related_name="games")
    host_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hosted_games",
    )
    host_token = models.CharField(max_length=64, unique=True, default=new_token)
    shortcode = models.CharField(max_length=SHORTCODE_LENGTH)
    status = models.CharField(max_length=12, choices=Status, default=Status.LOBBY)
    current_position = models.PositiveIntegerField(null=True, blank=True)
    # When the current kwestion opened. Null while showing its results.
    kwestion_opened_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    objects = GameSessionQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Codes only need to be unique among games people can still join,
            # so they can be reused once a game finishes.
            models.UniqueConstraint(
                fields=["shortcode"],
                condition=~models.Q(status="finished"),
                name="unique_shortcode_while_active",
            ),
        ]

    def __str__(self):
        return f"{self.shortcode} · {self.kwiz}"

    def get_absolute_url(self):
        return reverse("games:host", args=[self.shortcode])

    @classmethod
    def start_for(cls, kwiz, host_user=None):
        """Create a game in the lobby with a fresh, unused shortcode."""
        for _ in range(20):
            try:
                with transaction.atomic():
                    return cls.objects.create(
                        kwiz=kwiz, host_user=host_user, shortcode=new_shortcode()
                    )
            except IntegrityError:
                continue
        raise RuntimeError("Could not find a free shortcode.")

    # --- game state ------------------------------------------------------

    @property
    def phase(self):
        """lobby, kwestion (answering open), reveal (showing results), or finished."""
        if self.status == self.Status.LOBBY:
            return "lobby"
        if self.status == self.Status.FINISHED:
            return "finished"
        return "kwestion" if self.kwestion_opened_at else "reveal"

    def slots(self):
        return self.kwiz.ordered_slots()

    def current_slot(self):
        if self.current_position is None:
            return None
        return (
            self.slots()
            .filter(position=self.current_position)
            .select_related("kwiz")
            .prefetch_related("kwestion__choices")
            .first()
        )

    def ms_since_open(self, now=None):
        now = now or timezone.now()
        return int((now - self.kwestion_opened_at).total_seconds() * 1000)

    def advance(self):
        """Open the next kwestion, or finish the game after the last one."""
        now = timezone.now()
        slots = self.slots()
        if self.current_position is not None:
            slots = slots.filter(position__gt=self.current_position)
        next_slot = slots.first()
        if next_slot is None:
            self.status = self.Status.FINISHED
            self.kwestion_opened_at = None
            self.ended_at = now
        else:
            self.status = self.Status.IN_PROGRESS
            self.started_at = self.started_at or now
            self.current_position = next_slot.position
            self.kwestion_opened_at = now
        self.save()

    def close_kwestion(self):
        if self.kwestion_opened_at is not None:
            self.kwestion_opened_at = None
            self.save(update_fields=["kwestion_opened_at"])

    def end(self):
        self.status = self.Status.FINISHED
        self.kwestion_opened_at = None
        self.ended_at = timezone.now()
        self.save()

    def leaderboard(self, limit=None):
        players = self.players.order_by("-total_score", "joined_at")
        return players[:limit] if limit else players


class Player(models.Model):
    game_session = models.ForeignKey(
        GameSession, on_delete=models.CASCADE, related_name="players"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="plays",
    )
    nickname = models.CharField(max_length=30)
    session_token = models.CharField(max_length=64, unique=True, default=new_token)
    total_score = models.IntegerField(default=0)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["joined_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["game_session", "nickname"], name="unique_nickname_per_game"
            ),
        ]

    def __str__(self):
        return self.nickname

    @transaction.atomic
    def answer(self, choice_id):
        """Record this player's answer to the open kwestion.

        Returns the Response, or None if answering is closed, the choice isn't
        part of the current kwestion, or the player already answered.
        """
        game = GameSession.objects.select_for_update().get(pk=self.game_session_id)
        if game.phase != "kwestion":
            return None
        slot = game.current_slot()
        elapsed = game.ms_since_open()
        time_limit_ms = slot.time_limit_sec * 1000
        if elapsed > time_limit_ms + ANSWER_GRACE_MS:
            return None
        choice = AnswerChoice.objects.filter(
            pk=choice_id, kwestion_id=slot.kwestion_id
        ).first()
        if choice is None:
            return None
        # Kwestions that don't reveal the answer don't score either: a jump on
        # the leaderboard would show who got it right.
        points = (
            score(
                slot.points, choice.correctness, choice.points, time_limit_ms, elapsed
            )
            if slot.is_scored
            else 0
        )
        try:
            with transaction.atomic():
                response = Response.objects.create(
                    player=self,
                    kwiz_kwestion=slot,
                    answer_choice=choice,
                    response_ms=elapsed,
                    points_awarded=points,
                )
        except IntegrityError:
            return None
        Player.objects.filter(pk=self.pk).update(
            total_score=models.F("total_score") + points
        )
        self.refresh_from_db(fields=["total_score"])
        return response


class Response(models.Model):
    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="responses"
    )
    kwiz_kwestion = models.ForeignKey(
        KwizKwestion, on_delete=models.CASCADE, related_name="responses"
    )
    answer_choice = models.ForeignKey(
        AnswerChoice,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="responses",
    )
    response_ms = models.PositiveIntegerField()
    points_awarded = models.IntegerField(default=0)
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["player", "kwiz_kwestion"], name="one_response_per_kwestion"
            ),
        ]

    def __str__(self):
        return f"{self.player} → {self.answer_choice}"
