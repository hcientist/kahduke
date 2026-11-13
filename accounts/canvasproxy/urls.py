from allauth.socialaccount.providers.oauth2.urls import default_urlpatterns

from .provider import CanvasProxyProvider

urlpatterns = default_urlpatterns(CanvasProxyProvider)
