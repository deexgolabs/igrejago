"""Rotas PÚBLICAS (sem login) de `custom_forms`, montadas sob
`<slug:church_slug>/formularios/` em `church_crm/urls.py` — a igreja é
resolvida pelo slug na URL (`core.tenancy.PublicChurchMixin`), não pelo
usuário logado (não tem). `app_name` PRÓPRIO (`custom_forms_public`,
diferente de `custom_forms/urls.py`) — dois `include()` com o MESMO
`app_name` fazem o Django só conseguir reverter (`reverse()`/`{% url %}`)
as rotas do primeiro registrado, silenciosamente quebrando as do segundo
(confirmado na prática: ver `django.urls.get_resolver().namespace_dict`)."""

from django.urls import path

from custom_forms.views import PublicFormDoneView, PublicFormView

app_name = "custom_forms_public"

urlpatterns = [
    path("<slug:slug>/obrigado/", PublicFormDoneView.as_view(), name="public_done"),
    path("<slug:slug>/", PublicFormView.as_view(), name="public"),
]
