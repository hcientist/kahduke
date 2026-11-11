import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def clean_path(path):
    """Normalize a folder or file path inside a repo; reject `..` tricks."""
    parts = [p for p in path.strip().strip("/").split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise ValidationError("Paths can't contain . or .. parts.")
    return "/".join(parts)


class Repo(models.Model):
    """A GitHub repo (or one folder of it) that a user lets KahDuke read.

    `token` is a fine-grained personal access token scoped by its owner to
    this repo: Contents read-only, or read and write. Public repos can be
    read with no token. Proof of concept: the token is stored in plain text;
    see README for what a real deployment should do instead.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="repos"
    )
    full_name = models.CharField("repo", max_length=200, help_text="owner/name")
    branch = models.CharField(max_length=200, blank=True)
    path = models.CharField(
        max_length=500, blank=True, help_text="Folder within the repo; blank = root."
    )
    token = models.CharField(max_length=255, blank=True)
    can_write = models.BooleanField(
        default=False, help_text="Let KahDuke commit kwizzes back to this repo."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["full_name", "path"]

    def __str__(self):
        where = f"{self.full_name}/{self.path}" if self.path else self.full_name
        return f"{where} @ {self.branch}" if self.branch else where

    def html_url(self, file_path=""):
        target = file_path or self.path
        tree = "blob" if file_path else "tree"
        url = f"https://github.com/{self.full_name}"
        return f"{url}/{tree}/{self.branch}/{target}" if target else url


class LinkedFile(models.Model):
    """Which file in a Repo a kwiz came from (or was pushed to)."""

    kwiz = models.OneToOneField(
        "kwizzes.Kwiz", on_delete=models.CASCADE, related_name="repo_file"
    )
    repo = models.ForeignKey(Repo, on_delete=models.CASCADE, related_name="files")
    file_path = models.CharField(max_length=500)
    # The blob sha we last pulled or pushed; GitHub refuses a push whose sha
    # is stale, which is how a push notices someone changed the file.
    sha = models.CharField(max_length=64, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["repo", "file_path"], name="one_kwiz_per_repo_file"
            )
        ]

    def __str__(self):
        return f"{self.kwiz} ↔ {self.repo.full_name}/{self.file_path}"

    @property
    def html_url(self):
        return self.repo.html_url(self.file_path)
