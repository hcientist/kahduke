from django.contrib import admin

from .models import GameSession, Player, Response


class PlayerInline(admin.TabularInline):
    model = Player
    extra = 0
    fields = ("nickname", "user", "total_score", "joined_at")
    readonly_fields = ("joined_at",)


@admin.register(GameSession)
class GameSessionAdmin(admin.ModelAdmin):
    list_display = ("shortcode", "kwiz", "host_user", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("shortcode", "kwiz__title")
    readonly_fields = ("host_token",)
    inlines = [PlayerInline]


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "kwiz_kwestion",
        "answer_choice",
        "response_ms",
        "points_awarded",
    )
