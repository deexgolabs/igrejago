"""Rotas PÚBLICAS (sem login) de `sermons`, montadas sob
`<slug:church_slug>/sermoes/` em `church_crm/urls.py`. `app_name` PRÓPRIO
(`sermons_public`, diferente de `sermons/urls.py`) — dois `include()` com
o mesmo `app_name` fazem o Django só reverter as rotas do primeiro
registrado (mesmo motivo documentado em `events/public_urls.py`)."""

from django.urls import path

from sermons.views import SermonPublicListView

app_name = "sermons_public"

urlpatterns = [
    path("", SermonPublicListView.as_view(), name="list"),
]
