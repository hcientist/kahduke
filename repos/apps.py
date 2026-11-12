from django.apps import AppConfig


class ReposConfig(AppConfig):
    name = "repos"
    verbose_name = "GitHub repos"

    def ready(self):
        from . import signals  # noqa: F401
