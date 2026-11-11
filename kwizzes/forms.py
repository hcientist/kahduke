from django import forms

from .models import Kwiz


class KwizCreateForm(forms.ModelForm):
    class Meta:
        model = Kwiz
        fields = ["title", "description"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Data structures warm-up"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class MarkdownImportForm(forms.Form):
    """Paste Markdown or upload a .md file; the upload wins if both are given."""

    markdown = forms.CharField(
        label="Markdown",
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 18, "class": "mono", "spellcheck": "false"}
        ),
    )
    file = forms.FileField(label="Or upload a .md file", required=False)

    MAX_BYTES = 512 * 1024

    def clean(self):
        data = super().clean()
        upload = data.get("file")
        if upload:
            if upload.size > self.MAX_BYTES:
                raise forms.ValidationError("That file is over 512 KB.")
            try:
                data["markdown"] = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError as e:
                raise forms.ValidationError("That file isn't UTF-8 text.") from e
        if not data.get("markdown", "").strip():
            raise forms.ValidationError("Paste some Markdown or choose a file.")
        return data
