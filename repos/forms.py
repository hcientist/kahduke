import re

from django import forms

from . import github
from .models import REPO_NAME, Repo, clean_path

GITHUB_URL = re.compile(
    r"^(?:https?://)?github\.com/([^/\s]+/[^/\s]+?)(?:\.git)?"
    r"(?:/tree/([^/\s]+)(?:/(\S*))?)?/?$"
)


class RepoForm(forms.ModelForm):
    """Link a repo. If one of the user's app installations is on the repo's
    owner account, KahDuke goes through it; otherwise the repo must be
    public and is read-only."""

    class Meta:
        model = Repo
        fields = ["full_name", "branch", "path", "can_write"]
        labels = {
            "full_name": "Repo",
            "path": "Folder",
            "can_write": "Let KahDuke commit kwizzes to this repo",
        }
        help_texts = {
            "full_name": "owner/name, or paste the GitHub URL of the repo or a folder in it.",
            "branch": "Blank for the repo's default branch.",
            "path": "Blank for the whole repo.",
            "can_write": "",
        }
        widgets = {
            "full_name": forms.TextInput(
                attrs={"placeholder": "hcientist/cs347-kwizzes"}
            ),
        }

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner

    def _installation_for(self, name):
        account = name.split("/", 1)[0].lower()
        for installation in self.owner.github_installations.all():
            if installation.account_login.lower() == account:
                return installation
        return None

    def clean(self):
        data = super().clean()
        name = data.get("full_name", "").strip()
        if match := GITHUB_URL.match(name):
            name = match.group(1)
            data["branch"] = data.get("branch") or match.group(2) or ""
            data["path"] = data.get("path") or match.group(3) or ""
        if not REPO_NAME.match(name):
            self.add_error("full_name", "Use owner/name, like hcientist/kahduke.")
            return data
        try:
            data["path"] = clean_path(data.get("path", ""))
        except forms.ValidationError as e:
            self.add_error("path", e)
            return data

        self.instance.full_name = name
        self.instance.installation = self._installation_for(name)
        self.instance.can_write = data.get("can_write", False)
        if self.instance.can_write and self.instance.installation is None:
            self.add_error(
                "can_write",
                "Committing needs the KahDuke GitHub App installed on "
                f"{name.split('/')[0]}. Install it above, then link the repo.",
            )
            return data
        try:
            info = github.repo_info(name, self.instance.token())
        except github.GitHubError as e:
            installation = self.instance.installation
            if installation and e.status in (403, 404, 422):
                raise forms.ValidationError(
                    f"The KahDuke app on {installation} doesn't include {name}. "
                    "Add the repo to the installation on GitHub, then try again."
                ) from e
            raise forms.ValidationError(str(e)) from e
        data["full_name"] = info.get("full_name", name)
        data["branch"] = data.get("branch") or info.get("default_branch", "main")
        return data

    def save(self, commit=True):
        self.instance.owner = self.owner
        return super().save(commit)


class PushNewForm(forms.Form):
    kwiz = forms.ModelChoiceField(queryset=None, label="Kwiz")
    file_name = forms.CharField(
        required=False,
        label="File name",
        help_text="Blank to name it after the kwiz's title.",
    )

    def __init__(self, *args, kwizzes=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kwiz"].queryset = kwizzes

    def clean_file_name(self):
        try:
            return clean_path(self.cleaned_data["file_name"])
        except forms.ValidationError as e:
            raise forms.ValidationError(e.messages) from e
