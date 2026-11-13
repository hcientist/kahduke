from django.contrib import admin

from .models import Installation, LinkedFile, Repo


@admin.register(Installation)
class InstallationAdmin(admin.ModelAdmin):
    list_display = ["account_login", "account_type", "installation_id"]
    filter_horizontal = ["users"]


@admin.register(Repo)
class RepoAdmin(admin.ModelAdmin):
    list_display = ["full_name", "branch", "path", "owner", "installation", "can_write"]


@admin.register(LinkedFile)
class LinkedFileAdmin(admin.ModelAdmin):
    list_display = ["kwiz", "repo", "file_path", "synced_at"]
