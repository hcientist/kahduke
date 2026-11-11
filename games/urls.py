from django.urls import path

from . import views

app_name = "games"

urlpatterns = [
    path("host/<int:kwiz_id>/", views.host, name="start"),
    path("<str:shortcode>/host/", views.host_screen, name="host"),
    path("<str:shortcode>/play/", views.play_screen, name="play"),
]
