from django.contrib import admin

from .models import AnswerChoice, Kwestion, Kwiz, KwizKwestion


class KwizKwestionInline(admin.TabularInline):
    model = KwizKwestion
    extra = 0
    autocomplete_fields = ["kwestion"]


class AnswerChoiceInline(admin.TabularInline):
    model = AnswerChoice
    extra = 0


@admin.register(Kwiz)
class KwizAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "visibility",
        "reveal_mode",
        "remixed_from",
        "updated_at",
    )
    list_filter = ("visibility", "reveal_mode", "player_screen")
    search_fields = ("title", "description")
    readonly_fields = ("edit_token",)
    inlines = [KwizKwestionInline]


@admin.register(Kwestion)
class KwestionAdmin(admin.ModelAdmin):
    list_display = ("prompt", "owner", "scoring_mode", "default_points")
    list_filter = ("scoring_mode",)
    search_fields = ("prompt",)
    inlines = [AnswerChoiceInline]
