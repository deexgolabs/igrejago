from django.urls import path

from events.views import (
    EventCreateView,
    EventDeleteView,
    EventListView,
    EventUpdateView,
    MercadoPagoWebhookView,
    PagBankRegistrationWebhookView,
    RegistrationCheckInView,
    RegistrationExportView,
    RegistrationListView,
    RegistrationMarkPaidView,
    RegistrationPromoteView,
)

app_name = "events"

urlpatterns = [
    path("", EventListView.as_view(), name="manage_list"),
    path("novo/", EventCreateView.as_view(), name="create"),
    path("webhook/mercadopago/", MercadoPagoWebhookView.as_view(), name="mercadopago_webhook"),
    path("webhook/pagbank/", PagBankRegistrationWebhookView.as_view(), name="pagbank_webhook"),
    path("checkin/<uuid:token>/", RegistrationCheckInView.as_view(), name="checkin"),
    path("<slug:slug>/editar/", EventUpdateView.as_view(), name="update"),
    path("<slug:slug>/excluir/", EventDeleteView.as_view(), name="delete"),
    path("<slug:slug>/inscritos/", RegistrationListView.as_view(), name="registrations"),
    path("<slug:slug>/inscritos/exportar/", RegistrationExportView.as_view(), name="registrations_export"),
    path("<slug:slug>/inscritos/<int:pk>/pago/", RegistrationMarkPaidView.as_view(), name="registration_mark_paid"),
    path("<slug:slug>/inscritos/<int:pk>/promover/", RegistrationPromoteView.as_view(), name="registration_promote"),
]
