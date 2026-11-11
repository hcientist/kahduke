from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """A KahDuke account. Optional for every role: creators, hosts and players
    can all participate anonymously and claim their rows later (see
    accounts.signals)."""

    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=60, blank=True)

    def __str__(self):
        return self.display_name or self.username
