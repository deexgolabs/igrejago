from django.urls import path

from notifications.views import (
    EmailMessageCancelView,
    EmailQueueListView,
    MessageCancelView,
    MessageQueueListView,
    MessageResendView,
    MessageTemplateCreateView,
    MessageTemplateDeleteView,
    MessageTemplateListView,
    MessageTemplateUpdateView,
    PushSubscribeView,
    ResendConfirmationEmailView,
    ScheduledMessageCreateView,
    SMSMessageCancelView,
    SMSQueueListView,
    WhatsAppConnectionView,
    WhatsAppConnectView,
    WhatsAppDisconnectView,
    WhatsAppWebhookView,
)

app_name = "notifications"

urlpatterns = [
    path("", MessageQueueListView.as_view(), name="queue"),
    path("nova/", ScheduledMessageCreateView.as_view(), name="create"),
    path("<int:pk>/cancelar/", MessageCancelView.as_view(), name="cancel"),
    path("<int:pk>/reenviar/", MessageResendView.as_view(), name="resend"),

    path("email/", EmailQueueListView.as_view(), name="email_queue"),
    path("email/<int:pk>/cancelar/", EmailMessageCancelView.as_view(), name="email_cancel"),

    path("sms/", SMSQueueListView.as_view(), name="sms_queue"),
    path("sms/<int:pk>/cancelar/", SMSMessageCancelView.as_view(), name="sms_cancel"),

    path("modelos/", MessageTemplateListView.as_view(), name="template_list"),
    path("modelos/novo/", MessageTemplateCreateView.as_view(), name="template_create"),
    path("modelos/<int:pk>/editar/", MessageTemplateUpdateView.as_view(), name="template_update"),
    path("modelos/<int:pk>/excluir/", MessageTemplateDeleteView.as_view(), name="template_delete"),

    path("push/inscrever/", PushSubscribeView.as_view(), name="push_subscribe"),

    path("whatsapp/", WhatsAppConnectionView.as_view(), name="whatsapp_connection"),
    path("whatsapp/reenviar-confirmacao/", ResendConfirmationEmailView.as_view(), name="resend_confirmation"),
    path("whatsapp/conectar/", WhatsAppConnectView.as_view(), name="whatsapp_connect"),
    path("whatsapp/desconectar/", WhatsAppDisconnectView.as_view(), name="whatsapp_disconnect"),
    path("webhook/evolution/", WhatsAppWebhookView.as_view(), name="webhook"),
]
