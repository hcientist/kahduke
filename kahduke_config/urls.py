"""URL configuration for the KahDuke project."""

from django.contrib import admin
from django.urls import include, path
from ninja import NinjaAPI

from games import views as game_views
from games.api import router as games_router
from kwizzes import views as kwiz_views
from kwizzes.api import router as kwizzes_router

api = NinjaAPI(title="KahDuke API", version="1.0")
api.add_router("/", kwizzes_router)
api.add_router("/", games_router)

urlpatterns = [
    path("", kwiz_views.home, name="home"),
    path("play/", game_views.join, name="join"),
    path("kwizzes/", include("kwizzes.urls")),
    path("games/", include("games.urls")),
    path("repos/", include("repos.urls")),
    path("accounts/", include("allauth.urls")),
    path("api/", api.urls),
    path("admin/", admin.site.urls),
]
