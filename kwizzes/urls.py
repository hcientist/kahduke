from django.urls import path

from . import views

app_name = "kwizzes"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("library/", views.library, name="library"),
    path("bank/", views.bank, name="bank"),
    path("new/", views.create, name="create"),
    path("import/", views.import_markdown, name="import"),
    path("edit/<str:token>/", views.edit_by_token, name="edit_by_token"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.edit, name="edit"),
    path("<int:pk>/markdown/", views.export_markdown, name="export"),
    path("<int:pk>/import/", views.import_markdown, name="reimport"),
    path("<int:pk>/remix/", views.remix, name="remix"),
    path("<int:pk>/delete/", views.delete, name="delete"),
]
