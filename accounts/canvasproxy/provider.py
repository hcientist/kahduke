"""Sign in with Canvas, by way of the Canvas API proxy.

The proxy (github.com/hcientist/canvas-proxy) is an OAuth2 server in front
of Canvas: KahDuke is registered there as an app, on the `auth_only` tier,
and never holds a Canvas developer key or Canvas token. The proxy's token
response carries a Canvas-shaped `user` block, and the tier allows
/api/v1/users/self/profile, which adds the email and login id.
"""

from allauth.account.models import EmailAddress
from allauth.socialaccount.providers.base import ProviderAccount
from allauth.socialaccount.providers.oauth2.provider import OAuth2Provider

from .views import CanvasProxyOAuth2Adapter


class CanvasProxyAccount(ProviderAccount):
    def to_str(self):
        data = self.account.extra_data
        return data.get("name") or data.get("login_id") or super().to_str()

    def get_avatar_url(self):
        return self.account.extra_data.get("avatar_url")


class CanvasProxyProvider(OAuth2Provider):
    id = "canvasproxy"
    name = "Canvas"
    account_class = CanvasProxyAccount
    oauth2_adapter_class = CanvasProxyOAuth2Adapter

    def extract_uid(self, data):
        # global_id survives a Canvas shard move; id is what older proxies send.
        return str(data.get("global_id") or data["id"])

    def _email(self, data):
        email = data.get("primary_email") or ""
        if not email and "@" in (data.get("login_id") or ""):
            email = data["login_id"]
        return email

    def extract_common_fields(self, data):
        login = data.get("login_id") or ""
        return {
            "email": self._email(data) or None,
            "username": login.split("@")[0] or None,
            "first_name": (data.get("short_name") or "")[:150],
            "display_name": (data.get("name") or "")[:60],
        }

    def extract_email_addresses(self, data):
        email = self._email(data)
        # Students can change their Canvas email, so it isn't proof of
        # owning the address; allauth won't use it to merge accounts.
        return (
            [EmailAddress(email=email, verified=False, primary=True)] if email else []
        )

    def get_default_scope(self):
        return []


provider_classes = [CanvasProxyProvider]
