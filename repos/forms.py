import re

from django import forms

from . import github
from .models import REPO_NAME, Repo, clean_path

GITHUB_URL = re.compile(
    r"^(?:https?://)?github\.com/([^/\s]+/[^/\s]+?)(?:\.git)?"
    r"(?:/tree/([^/\s]+)(?:/(\S*))?)?/?$"
)


class RepoForm(forms.ModelForm):
    class Meta:
        model = Repo
        fields = ["full_name", "branch", "path", "token", "can_write"]
        labels = {
            "full_name": "Repo",
            "path": "Folder",
            "token": "Access token",
            "can_write": "Let KahDuke commit kwizzes to this repo",
        }
        help_texts = {
            "full_name": "owner/name, or paste the GitHub URL of the repo or a folder in it.",
            "branch": "Blank for the repo's default branch.",
            "path": "Blank for the whole repo.",
            "token": "Blank for a public repo you only read from.",
            "can_write": "",
        }
        widgets = {
            "full_name": forms.TextInput(
                attrs={"placeholder": "hcientist/cs347-kwizzes"}
            ),
            "token": forms.PasswordInput(attrs={"autocomplete": "off"}),
        }

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner

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
        data["full_name"] = name
        try:
            data["path"] = clean_path(data.get("path", ""))
        except forms.ValidationError as e:
            self.add_error("path", e)
            return data
        token = data.get("token", "").strip()
        data["token"] = token
        if data.get("can_write") and not token:
            self.add_error("token", "Writing to a repo needs a token.")
            return data
        try:
            info = github.repo_info(name, token)
        except github.GitHubError as e:
            raise forms.ValidationError(str(e)) from e
        data["full_name"] = info.get("full_name", name)
        data["branch"] = data.get("branch") or info.get("default_branch", "main")
        for field in ("full_name", "branch", "path", "token"):
            setattr(self.instance, field, data[field])
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
