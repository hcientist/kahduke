from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.urls import reverse
from ninja import Router
from ninja.errors import HttpError
from ninja.security import APIKeyCookie, django_auth

from .access import KWIZ_TOKENS, can_edit_kwiz, can_view_kwiz, remember_token
from .editing import save_kwiz
from .models import Kwestion, Kwiz
from .schemas import KwestionOut, KwizIn, KwizOut, KwizSummary, RemixOut


class SessionWithCsrf(APIKeyCookie):
    """Admit any visitor who has a session, signed in or not, and enforce CSRF.

    Anonymous creators prove ownership with tokens kept in their session, so
    write endpoints can't require a signed-in user, but they still must not be
    callable cross-site.
    """

    param_name = settings.SESSION_COOKIE_NAME

    def authenticate(self, request, key):
        return request.user if request.user.is_authenticated else True


session_csrf = SessionWithCsrf()

router = Router(tags=["kwizzes"])


def _kwiz_out(request, kwiz):
    out = KwizOut.from_orm(kwiz)
    out.can_edit = can_edit_kwiz(request, kwiz)
    return out


@router.get("/kwizzes/public", response=list[KwizSummary])
def public_kwizzes(request, q: str = ""):
    kwizzes = (
        Kwiz.objects.filter(visibility=Kwiz.Visibility.PUBLIC)
        .select_related("owner")
        .annotate(kwestion_count=Count("slots"))
    )
    if q:
        kwizzes = kwizzes.filter(Q(title__icontains=q) | Q(description__icontains=q))
    return kwizzes[:100]


@router.get("/kwizzes/{kwiz_id}", response=KwizOut)
def get_kwiz(request, kwiz_id: int):
    kwiz = get_object_or_404(Kwiz.objects.select_related("owner"), pk=kwiz_id)
    if not can_view_kwiz(request, kwiz):
        raise HttpError(404, "Not found")
    return _kwiz_out(request, kwiz)


@router.put("/kwizzes/{kwiz_id}", response=KwizOut, auth=session_csrf)
def update_kwiz(request, kwiz_id: int, payload: KwizIn):
    kwiz = get_object_or_404(Kwiz, pk=kwiz_id)
    if not can_edit_kwiz(request, kwiz):
        raise HttpError(403, "You can't edit this kwiz.")
    try:
        save_kwiz(kwiz, payload, request.user)
    except PermissionDenied as e:
        raise HttpError(403, str(e)) from e
    except ValidationError as e:
        raise HttpError(422, " ".join(e.messages)) from e
    return _kwiz_out(request, Kwiz.objects.get(pk=kwiz.pk))


@router.post("/kwizzes/{kwiz_id}/remix", response=RemixOut, auth=session_csrf)
def remix_kwiz(request, kwiz_id: int):
    kwiz = get_object_or_404(Kwiz, pk=kwiz_id)
    if not can_view_kwiz(request, kwiz):
        raise HttpError(404, "Not found")
    owner = request.user if request.user.is_authenticated else None
    copy = kwiz.remix(owner=owner)
    remember_token(request.session, KWIZ_TOKENS, copy.edit_token)
    return {"id": copy.pk, "edit_url": reverse("kwizzes:edit", args=[copy.pk])}


@router.get("/kwestions/bank", response=list[KwestionOut], auth=django_auth)
def kwestion_bank(request, q: str = ""):
    kwestions = (
        Kwestion.objects.filter(owner=request.user)
        .annotate(used_in=Count("slots__kwiz", distinct=True))
        .prefetch_related("choices")
    )
    if q:
        kwestions = kwestions.filter(prompt__icontains=q)
    return kwestions
