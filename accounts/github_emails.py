"""Let GitHub sign-in work when /user/emails is off limits.

KahDuke signs users in with its GitHub App's credentials. An app's user
token can read /user/emails only if the app has the "Email addresses"
account permission; without it GitHub answers 403, which allauth's GitHub
provider treats as fatal. Treat it like the 404 allauth already allows: no
email list, so the user just types one on the sign-up form.
"""

from http import HTTPStatus

from allauth.socialaccount.providers.github.views import GitHubOAuth2Adapter


def _tolerant_get_emails(self, headers):
    from allauth.socialaccount.adapter import get_adapter

    with get_adapter().get_requests_session() as session:
        resp = session.get(self.emails_url, headers=headers)
    if resp.status_code in (HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND):
        return None
    resp.raise_for_status()
    return resp.json()


def install():
    GitHubOAuth2Adapter.get_emails = _tolerant_get_emails
