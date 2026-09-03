from django.urls import path

from notifications.views import (
    EmailClickTrackingView,
    EmailMessageCancelView,
    EmailOpenTrackingView,
    EmailQueueListView,
    EmailUnsubscribeView,
    MessageCancelView,
    MessageQueueListView,
    MessageResendView,
    MessageTemplateCreateView,
    MessageTemplateDeleteView,
    MessageTemplateListView,
    MessageTemplateUpdateView,
    MetaWhatsAppWebhookView,
    PushSubscribeView,
    ResendConfirmationEmailView,
    ScheduledMessageCreateView,
    SMSMessageCancelView,
    SMSQueueListView,
    WhatsAppConnectionView,
    WhatsAppConnectView,
    WhatsAppDisconnectView,
    WhatsAppMetaConfigView,
    WhatsAppMetaTemplateCreateView,
    WhatsAppMetaTemplateDeleteView,
    WhatsAppMetaTemplateListView,
    WhatsAppMetaTemplateRefreshStatusView,
    WhatsAppMetaTemplateSubmitView,
    WhatsAppMetaTemplateUpdateView,
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
    path("email/rastrear/<uuid:token>.gif", EmailOpenTrackingView.as_view(), name="email_open_tracking"),
    path("email/clique/<uuid:token>/", EmailClickTrackingView.as_view(), name="email_click_tracking"),
    path("email/cancelar/<uuid:token>/", EmailUnsubscribeView.as_view(), name="email_unsubscribe"),

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
    path("whatsapp/canal/", WhatsAppMetaConfigView.as_view(), name="whatsapp_meta_config"),
    path("whatsapp/templates/", WhatsAppMetaTemplateListView.as_view(), name="whatsapp_meta_templates"),
    path("whatsapp/templates/novo/", WhatsAppMetaTemplateCreateView.as_view(), name="whatsapp_meta_template_create"),
    path(
        "whatsapp/templates/<int:pk>/editar/",
        WhatsAppMetaTemplateUpdateView.as_view(),
        name="whatsapp_meta_template_update",
    ),
    path(
        "whatsapp/templates/<int:pk>/excluir/",
        WhatsAppMetaTemplateDeleteView.as_view(),
        name="whatsapp_meta_template_delete",
    ),
    path(
        "whatsapp/templates/<int:pk>/enviar/",
        WhatsAppMetaTemplateSubmitView.as_view(),
        name="whatsapp_meta_template_submit",
    ),
    path(
        "whatsapp/templates/<int:pk>/atualizar-status/",
        WhatsAppMetaTemplateRefreshStatusView.as_view(),
        name="whatsapp_meta_template_refresh_status",
    ),
    path("webhook/evolution/", WhatsAppWebhookView.as_view(), name="webhook"),
    path("webhook/meta/", MetaWhatsAppWebhookView.as_view(), name="webhook_meta"),
]
