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
