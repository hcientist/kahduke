from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        from . import github_emails, signals  # noqa: F401

        github_emails.install()
