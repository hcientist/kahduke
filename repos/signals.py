"""Signing in with GitHub doubles as connecting the GitHub App.

GitHub sign-in uses the app's own OAuth credentials (see settings), so the
user token allauth gets is an app user token, the same kind
repos.views.github_callback asks for. While it's in hand, ask GitHub which
installations this user can access, and save the trip to the GitHub page.
"""

import logging

from allauth.account.signals import user_logged_in
from allauth.socialaccount.signals import social_account_added
from django.conf import settings
from django.dispatch import receiver

from . import github, sync

log = logging.getLogger(__name__)


def _record(user, sociallogin):
    if sociallogin is None or sociallogin.account.provider != "github":
        return
    token = sociallogin.token
    if not github.app_enabled() or token is None:
        return
    if (
        token.app is not None
        and token.app.client_id != settings.GITHUB_APP["CLIENT_ID"]
    ):
        return  # a separate OAuth app's token can't list app installations
    try:
        sync.remember_installations(user, github.user_installations(token.token))
    except github.GitHubError:
        log.warning("Couldn't list GitHub App installations at sign-in", exc_info=True)


@receiver(user_logged_in)
def installations_on_login(sender, request, user, sociallogin=None, **kwargs):
    _record(user, sociallogin)


@receiver(social_account_added)
def installations_on_connect(sender, request, sociallogin, **kwargs):
    _record(sociallogin.user, sociallogin)
