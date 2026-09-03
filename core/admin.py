from django.contrib import admin

from core.models import AuditLog, Church, DataDeletionRequest


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "user", "church", "action", "model_name", "object_repr")
    list_filter = ("action", "model_name", "church")
    search_fields = ("object_repr",)
    date_hierarchy = "timestamp"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DataDeletionRequest)
class DataDeletionRequestAdmin(admin.ModelAdmin):
    """Espelho, pro dono da plataforma, da fila que cada igreja processa
    em `/privacidade/solicitacoes/` (`core.views.DataDeletionRequestListView`)."""

    list_display = ("person_name", "church", "status", "requested_at", "processed_by")
    list_filter = ("status", "church")
    readonly_fields = ("person", "person_name", "requested_at", "church")

    def has_add_permission(self, request):
        return False


@admin.register(Church)
class ChurchAdmin(admin.ModelAdmin):
    """Painel do dono da plataforma — vê e gerencia TODAS as igrejas
    (multi-tenência: `Church` não é um `TenantModel`, então
    `Church.objects` nunca é filtrado). A conexão Evolution API em si
    (criar/QR code/status/desconectar) mudou de dono — uma igreja pode
    ter mais de um número agora, então isso vive em
    `notifications.admin.WhatsAppInstanceAdmin`, uma linha por
    instância (acessível pelo link "Instâncias de WhatsApp" no admin,
    ou filtrando por igreja lá)."""

    list_display = ("name", "slug", "status", "plano", "pastor_name")
    list_filter = ("status", "plano")
    search_fields = ("name", "slug")
    readonly_fields = ("slug",)
    fieldsets = (
        ("Igreja", {"fields": ("name", "slug", "pastor_name", "logo", "brand_color")}),
        ("Plano/cobrança", {"fields": (
            "status", "plano", "trial_expira_em", "email_confirmed",
            "gateway_customer_id", "gateway_subscription_id",
        )}),
        ("WhatsApp — mensagens e limite de números", {"fields": (
            "whatsapp_absence_template", "whatsapp_birthday_template",
            "whatsapp_max_instancias",
        )}),
        ("WhatsApp — canal Meta Cloud (ritmo de envio, sem conceito de instância)", {"fields": (
            "whatsapp_send_interval_seconds", "whatsapp_batch_size",
        )}),
        ("PIX (pagamento de eventos)", {"fields": (
            "pix_key", "pix_key_type", "pix_receiver_name", "pix_receiver_city",
        )}),
        ("Gateways de pagamento (opcional — confirmação automática)", {"fields": (
            "mercadopago_access_token", "pagbank_token",
        )}),
    )
