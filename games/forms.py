from django import forms

from .models import SHORTCODE_LENGTH, GameSession


class JoinForm(forms.Form):
    shortcode = forms.CharField(
        label="Game code",
        min_length=SHORTCODE_LENGTH,
        max_length=SHORTCODE_LENGTH,
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "off",
                "pattern": r"\d*",
                "placeholder": "000000",
            }
        ),
    )
    nickname = forms.CharField(
        max_length=30, widget=forms.TextInput(attrs={"autocomplete": "nickname"})
    )

    def clean_shortcode(self):
        code = self.cleaned_data["shortcode"].strip()
        self.game = GameSession.objects.active().filter(shortcode=code).first()
        if self.game is None:
            raise forms.ValidationError(
                "No game is running with that code. Check the code on the host's screen."
            )
        return code

    def clean(self):
        cleaned = super().clean()
        nickname = cleaned.get("nickname", "").strip()
        game = getattr(self, "game", None)
        if (
            game
            and nickname
            and game.players.filter(nickname__iexact=nickname).exists()
        ):
            self.add_error("nickname", "Someone in this game already has that name.")
        cleaned["nickname"] = nickname
        return cleaned
