from django.urls import path

from core.views import (
    AssinaturaCheckoutView,
    AssinaturaView,
    AssinaturaWebhookView,
    AuditLogListView,
    ChurchSignupDoneView,
    ChurchSignupView,
    ConfirmEmailView,
    ContaSuspensaView,
    DashboardView,
    DataDeletionRequestListView,
    DataDeletionRequestProcessView,
    GeneralReportPDFView,
    MeusDadosExportView,
    MeusDadosView,
    PrivacyPolicyView,
    SettingsView,
    SolicitarExclusaoView,
    health_check,
    manifest_json,
    service_worker_js,
)

app_name = "core"

urlpatterns = [
    path("health/", health_check, name="health_check"),
    path("manifest.json", manifest_json, name="manifest"),
    path("sw.js", service_worker_js, name="service_worker"),
    path("relatorio.pdf", GeneralReportPDFView.as_view(), name="report_pdf"),
    path("configuracoes/", SettingsView.as_view(), name="settings"),
    path("auditoria/", AuditLogListView.as_view(), name="audit_log"),
    path("cadastro-igreja/", ChurchSignupView.as_view(), name="church_signup"),
    path("cadastro-igreja/obrigado/", ChurchSignupDoneView.as_view(), name="church_signup_done"),
    path("cadastro-igreja/confirmar/<str:token>/", ConfirmEmailView.as_view(), name="confirm_email"),
    path("conta-suspensa/", ContaSuspensaView.as_view(), name="conta_suspensa"),
    path("privacidade/", PrivacyPolicyView.as_view(), name="privacy_policy"),
    path("meus-dados/", MeusDadosView.as_view(), name="meus_dados"),
    path("meus-dados/baixar/", MeusDadosExportView.as_view(), name="meus_dados_export"),
    path("meus-dados/solicitar-exclusao/", SolicitarExclusaoView.as_view(), name="solicitar_exclusao"),
    path("privacidade/solicitacoes/", DataDeletionRequestListView.as_view(), name="data_deletion_requests"),
    path(
        "privacidade/solicitacoes/<int:pk>/confirmar/",
        DataDeletionRequestProcessView.as_view(),
        name="data_deletion_request_process",
    ),
    path("assinatura/", AssinaturaView.as_view(), name="assinatura"),
    path("assinatura/assinar/<str:plano_key>/", AssinaturaCheckoutView.as_view(), name="assinatura_checkout"),
    path("assinatura/webhook/mercadopago/", AssinaturaWebhookView.as_view(), name="assinatura_webhook"),
    path("", DashboardView.as_view(), name="dashboard"),
]
