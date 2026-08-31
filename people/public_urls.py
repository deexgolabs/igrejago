"""Rotas PÚBLICAS (sem login) de `people`, montadas sob
`<slug:church_slug>/pessoas/` em `church_crm/urls.py` — a igreja é
resolvida pelo slug na URL (`core.tenancy.PublicChurchMixin`), não pelo
usuário logado (não tem). `app_name` PRÓPRIO (`people_public`, diferente
de `people/urls.py`) — dois `include()` com o MESMO `app_name` fazem o
Django só conseguir reverter (`reverse()`/`{% url %}`) as rotas do
primeiro registrado, silenciosamente quebrando as do segundo (confirmado
na prática: ver `django.urls.get_resolver().namespace_dict`)."""

from django.urls import path

from people.views import PublicVisitorSignupDoneView, PublicVisitorSignupView

app_name = "people_public"

urlpatterns = [
    path("cadastro/", PublicVisitorSignupView.as_view(), name="public_signup"),
    path("cadastro/obrigado/", PublicVisitorSignupDoneView.as_view(), name="public_signup_done"),
]
