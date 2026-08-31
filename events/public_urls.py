"""Rotas PÚBLICAS (sem login) de `events`, montadas sob
`<slug:church_slug>/eventos/` em `church_crm/urls.py` — a igreja é
resolvida pelo slug na URL (`core.tenancy.PublicChurchMixin`), não pelo
usuário logado (não tem). `app_name` PRÓPRIO (`events_public`, diferente
de `events/urls.py`) — dois `include()` com o MESMO `app_name` fazem o
Django só conseguir reverter (`reverse()`/`{% url %}`) as rotas do
primeiro registrado, silenciosamente quebrando as do segundo (confirmado
na prática, não só documentação: ver `django.urls.get_resolver().namespace_dict`)."""

from django.urls import path

from events.views import (
    EventDetailView,
    EventRegistrationView,
    MercadoPagoCheckoutStartView,
    RegistrationDoneView,
    RegistrationPaymentView,
)

app_name = "events_public"

urlpatterns = [
    path("<slug:slug>/", EventDetailView.as_view(), name="detail"),
    path("<slug:slug>/inscricao/", EventRegistrationView.as_view(), name="register"),
    path("<slug:slug>/inscricao/<int:pk>/pagamento/", RegistrationPaymentView.as_view(), name="register_payment"),
    path(
        "<slug:slug>/inscricao/<int:pk>/pagamento/mercadopago/",
        MercadoPagoCheckoutStartView.as_view(),
        name="mercadopago_checkout_start",
    ),
    path("<slug:slug>/inscricao/<int:pk>/obrigado/", RegistrationDoneView.as_view(), name="register_done"),
]
