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


class Installation(models.Model):
    """One installation of the KahDuke GitHub App on a user or org account.

    `users` are the KahDuke accounts that proved, by signing in with GitHub,
    that GitHub lets them see this installation. Only they can link its repos.
    """

    installation_id = models.BigIntegerField(unique=True)
    account_login = models.CharField(max_length=100)
    account_type = models.CharField(max_length=20, blank=True)
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="github_installations", blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["account_login"]

    def __str__(self):
        return self.account_login

    @property
    def settings_url(self):
        if self.account_type == "Organization":
            return (
                f"https://github.com/organizations/{self.account_login}"
                f"/settings/installations/{self.installation_id}"
            )
        return f"https://github.com/settings/installations/{self.installation_id}"


class Repo(models.Model):
    """A GitHub repo (or one folder of it) that a user reads kwizzes from.

    With an `installation`, KahDuke reaches the repo through the GitHub App,
    asking each time for a short-lived token limited to this one repo, with
    Contents read, or write when it's committing and `can_write` is on.
    Without one, the repo must be public, and it's read-only.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="repos"
    )
    installation = models.ForeignKey(
        Installation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="repos",
    )
    full_name = models.CharField("repo", max_length=200, help_text="owner/name")
    branch = models.CharField(max_length=200, blank=True)
    path = models.CharField(
        max_length=500, blank=True, help_text="Folder within the repo; blank = root."
    )
    can_write = models.BooleanField(
        default=False, help_text="Let KahDuke commit kwizzes back to this repo."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["full_name", "path"]

    def __str__(self):
        where = f"{self.full_name}/{self.path}" if self.path else self.full_name
        return f"{where} @ {self.branch}" if self.branch else where

    @property
    def name(self):
        return self.full_name.split("/", 1)[1]

    def token(self, write=False):
        """A token for this repo alone, or "" to call GitHub anonymously."""
        from . import github

        if self.installation is None:
            if write:
                raise github.GitHubError(
                    "Committing needs the KahDuke GitHub App installed on this repo."
                )
            return ""
        if write and not self.can_write:
            raise github.GitHubError("This repo is linked read-only.")
        return github.installation_token(
            self.installation.installation_id, self.name, write=write
        )

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
