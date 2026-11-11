from django.contrib import admin

from .models import LinkedFile, Repo


@admin.register(Repo)
class RepoAdmin(admin.ModelAdmin):
    list_display = ["full_name", "branch", "path", "owner", "can_write"]
    exclude = ["token"]


@admin.register(LinkedFile)
class LinkedFileAdmin(admin.ModelAdmin):
    list_display = ["kwiz", "repo", "file_path", "synced_at"]
