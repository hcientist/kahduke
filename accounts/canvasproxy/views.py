import logging

from allauth.socialaccount import app_settings
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.providers.oauth2.views import (
    OAuth2Adapter,
    OAuth2CallbackView,
    OAuth2LoginView,
)

log = logging.getLogger(__name__)


class CanvasProxyOAuth2Adapter(OAuth2Adapter):
    provider_id = "canvasproxy"

    @property
    def base_url(self):
        settings = app_settings.PROVIDERS.get(self.provider_id, {})
        return settings.get("BASE_URL", "").rstrip("/")

    @property
    def authorize_url(self):
        return f"{self.base_url}/oauth2/auth"

    @property
    def access_token_url(self):
        return f"{self.base_url}/oauth2/token"

    @property
    def profile_url(self):
        return f"{self.base_url}/api/v1/users/self/profile"

    @property
    def revoke_url(self):
        return f"{self.base_url}/oauth2/revoke"

    def complete_login(self, request, app, token, **kwargs):
        response = kwargs.get("response") or {}
        data = dict(response.get("user") or {})
        headers = {"Authorization": f"Bearer {token.token}"}
        with get_adapter().get_requests_session() as session:
            resp = session.get(self.profile_url, headers=headers)
            resp.raise_for_status()
            profile = resp.json()
            # KahDuke only needed to know who this is; give the grant back.
            try:
                session.post(
                    self.revoke_url,
                    data={
                        "token": token.token,
                        "client_id": app.client_id,
                        "client_secret": app.secret,
                    },
                )
            except Exception:
                log.warning("Couldn't revoke the Canvas proxy token", exc_info=True)
        data |= {k: v for k, v in profile.items() if v not in (None, "")}
        data.setdefault("global_id", (response.get("user") or {}).get("global_id"))
        return self.get_provider().sociallogin_from_response(request, data)


oauth2_login = OAuth2LoginView.adapter_view(CanvasProxyOAuth2Adapter)
oauth2_callback = OAuth2CallbackView.adapter_view(CanvasProxyOAuth2Adapter)
