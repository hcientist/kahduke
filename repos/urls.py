from django.urls import path

from . import views

app_name = "repos"

urlpatterns = [
    path("", views.repo_list, name="list"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/import/", views.import_file, name="import"),
    path("<int:pk>/push-new/", views.push_new, name="push_new"),
    path("<int:pk>/delete/", views.delete, name="delete"),
    path("linked/<int:pk>/pull/", views.pull, name="pull"),
    path("linked/<int:pk>/push/", views.push, name="push"),
    path("linked/<int:pk>/unlink/", views.unlink, name="unlink"),
]
