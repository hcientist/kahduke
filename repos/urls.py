from django.urls import path

from . import views

app_name = "repos"

urlpatterns = [
    path("", views.repo_list, name="list"),
    path("github/install/", views.github_install, name="github_install"),
    path("github/connect/", views.github_connect, name="github_connect"),
    path("github/callback/", views.github_callback, name="github_callback"),
    path("github/webhook/", views.github_webhook, name="github_webhook"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/import/", views.import_file, name="import"),
    path("<int:pk>/push-new/", views.push_new, name="push_new"),
    path("<int:pk>/delete/", views.delete, name="delete"),
    path("linked/<int:pk>/pull/", views.pull, name="pull"),
    path("linked/<int:pk>/push/", views.push, name="push"),
    path("linked/<int:pk>/unlink/", views.unlink, name="unlink"),
]
