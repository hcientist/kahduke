"""Social sign-in: GitHub and Canvas (through the Canvas API proxy).

Each test drives allauth's real OAuth2 views; only the HTTP calls to the
outside world are faked, at requests.Session.request.
"""

import json
from unittest import mock
from urllib.parse import parse_qs, urlparse

import requests
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from kwizzes.access import KWIZ_TOKENS
from kwizzes.models import Kwiz
from repos.models import Installation
from repos.tests import APP

User = get_user_model()

PROXY = "https://canvas.example.edu"
PROVIDERS = {
    "github": {
        "APPS": [{"client_id": APP["CLIENT_ID"], "secret": APP["CLIENT_SECRET"]}],
        "SCOPE": ["read:user", "user:email"],
    },
    "canvasproxy": {
        "BASE_URL": PROXY,
        "APPS": [{"client_id": "kahduke", "secret": "proxy-secret"}],
    },
}


def fake_response(status, data):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(data).encode()
    response.headers["Content-Type"] = "application/json"
    return response


class FakeServers:
    """Answers the calls allauth and KahDuke make to GitHub and the proxy."""

    def __init__(self):
        self.calls = []
        self.github_emails = (
            403,
            {"message": "Resource not accessible by integration"},
        )
        self.canvas_profile = {
            "id": 7,
            "name": "Ada Lovelace",
            "short_name": "Ada",
            "primary_email": "lovelaad@dukes.jmu.edu",
            "login_id": "lovelaad",
            "avatar_url": "https://canvas.example.edu/a.png",
        }

    def __call__(self, session, method, url, **kwargs):
        self.calls.append((method, url))
        path = urlparse(url).path
        host = urlparse(url).netloc
        if host == "github.com" and path == "/login/oauth/access_token":
            return fake_response(
                200, {"access_token": "ghu_user", "token_type": "bearer"}
            )
        if host == "api.github.com" and path == "/user":
            return fake_response(200, {"id": 99, "login": "ada", "name": "Ada L."})
        if host == "api.github.com" and path == "/user/emails":
            return fake_response(*self.github_emails)
        if url.startswith(PROXY):
            if path == "/oauth2/token":
                assert kwargs["data"]["client_secret"] == "proxy-secret"
                return fake_response(
                    200,
                    {
                        "access_token": "proxy-access",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "user": {
                            "id": 7,
                            "name": "Ada Lovelace",
                            "global_id": "1000007",
                        },
                    },
                )
            if path == "/api/v1/users/self/profile":
                assert kwargs["headers"]["Authorization"] == "Bearer proxy-access"
                return fake_response(200, self.canvas_profile)
            if path == "/oauth2/revoke":
                return fake_response(200, {})
        raise AssertionError(f"unexpected {method} {url}")


@override_settings(SOCIALACCOUNT_PROVIDERS=PROVIDERS, GITHUB_APP=APP)
class SocialLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.servers = FakeServers()
        patcher = mock.patch.object(
            requests.Session, "request", autospec=True, side_effect=self.servers
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.installations = mock.patch(
            "repos.github.user_installations",
            return_value=[{"id": 42, "account": {"login": "ada", "type": "User"}}],
        )
        self.user_installations = self.installations.start()
        self.addCleanup(self.installations.stop)

    def sign_in(self, provider):
        response = self.client.post(reverse(f"{provider}_login"))
        location = urlparse(response["Location"])
        query = parse_qs(location.query)
        return location, self.client.get(
            reverse(f"{provider}_callback"),
            {"code": "the-code", "state": query["state"][0]},
        )

    def test_sign_in_page_offers_both(self):
        response = self.client.get(reverse("account_login"))
        self.assertContains(response, "GitHub")
        self.assertContains(response, "Canvas")

    @override_settings(SOCIALACCOUNT_PROVIDERS={})
    def test_unconfigured_providers_are_hidden(self):
        response = self.client.get(reverse("account_login"))
        self.assertNotContains(response, "Canvas")

    def test_canvas_sign_in_creates_an_account(self):
        location, response = self.sign_in("canvasproxy")
        self.assertEqual(
            f"{location.scheme}://{location.netloc}{location.path}",
            f"{PROXY}/oauth2/auth",
        )
        self.assertRedirects(
            response, reverse("kwizzes:dashboard"), fetch_redirect_response=False
        )
        user = User.objects.get()
        self.assertEqual(
            (user.username, user.email, user.display_name),
            ("lovelaad", "lovelaad@dukes.jmu.edu", "Ada Lovelace"),
        )
        account = SocialAccount.objects.get()
        self.assertEqual((account.provider, account.uid), ("canvasproxy", "1000007"))
        self.assertIn(("POST", f"{PROXY}/oauth2/revoke"), self.servers.calls)

    def test_canvas_sign_in_again_finds_the_same_account(self):
        self.sign_in("canvasproxy")
        self.client.logout()
        self.servers.canvas_profile["name"] = "Ada King"
        self.sign_in("canvasproxy")
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(
            int(self.client.session["_auth_user_id"]), User.objects.get().pk
        )

    def test_canvas_sign_in_claims_anonymous_kwizzes(self):
        kwiz = Kwiz.objects.create(title="Made before signing in")
        session = self.client.session
        session[KWIZ_TOKENS] = [kwiz.edit_token]
        session.save()
        self.sign_in("canvasproxy")
        kwiz.refresh_from_db()
        self.assertEqual(kwiz.owner, User.objects.get())

    def test_github_sign_in_without_email_asks_for_one(self):
        _, response = self.sign_in("github")
        self.assertRedirects(
            response, reverse("socialaccount_signup"), fetch_redirect_response=False
        )
        response = self.client.post(
            reverse("socialaccount_signup"),
            {"email": "ada@example.com", "username": "ada"},
        )
        user = User.objects.get()
        self.assertEqual((user.email, user.display_name), ("ada@example.com", "Ada L."))
        self.assertEqual(SocialAccount.objects.get().uid, "99")
        # The app user token was used to pick up the user's installations.
        self.user_installations.assert_called_with("ghu_user")
        self.assertEqual(
            list(user.github_installations.all()), [Installation.objects.get()]
        )

    def test_github_sign_in_with_a_verified_email(self):
        self.servers.github_emails = (
            200,
            [{"email": "ada@example.com", "primary": True, "verified": True}],
        )
        _, response = self.sign_in("github")
        self.assertRedirects(
            response, reverse("kwizzes:dashboard"), fetch_redirect_response=False
        )
        self.assertEqual(User.objects.get().email, "ada@example.com")

    def test_connect_github_to_an_existing_account(self):
        user = User.objects.create_user(
            username="ada", email="ada@example.com", password="pw"
        )
        self.client.force_login(user)
        response = self.client.post(reverse("github_login"), {"process": "connect"})
        state = parse_qs(urlparse(response["Location"]).query)["state"][0]
        self.client.get(reverse("github_callback"), {"code": "c", "state": state})
        self.assertEqual(SocialAccount.objects.get().user, user)
        self.assertEqual(user.github_installations.count(), 1)

    @override_settings(
        SOCIALACCOUNT_PROVIDERS={
            "github": {"APPS": [{"client_id": "separate-oauth-app", "secret": "s"}]}
        }
    )
    def test_a_separate_oauth_app_does_not_list_installations(self):
        self.sign_in("github")
        self.user_installations.assert_not_called()
