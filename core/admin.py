import secrets

from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.html import format_html

from core import whatsapp
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
    `Church.objects` nunca é filtrado). Cada igreja tem sua própria
    instância no servidor Evolution API compartilhado (`settings.EVOLUTION_API_URL`)
    — a conexão em si (criar/QR code/status/desconectar) é feita aqui,
    por igreja."""

    list_display = ("name", "slug", "status", "plano", "pastor_name")
    list_filter = ("status", "plano")
    search_fields = ("name", "slug")
    readonly_fields = ("whatsapp_connection_panel", "slug")
    fieldsets = (
        ("Igreja", {"fields": ("name", "slug", "pastor_name", "logo", "brand_color")}),
        ("Plano/cobrança", {"fields": (
            "status", "plano", "trial_expira_em", "email_confirmed",
            "gateway_customer_id", "gateway_subscription_id",
        )}),
        ("WhatsApp — mensagens", {"fields": (
            "whatsapp_absence_template", "whatsapp_birthday_template",
            "whatsapp_send_interval_seconds", "whatsapp_batch_size",
        )}),
        ("WhatsApp — conexão (instância nesta igreja)", {"fields": (
            "whatsapp_instance", "whatsapp_instance_token", "whatsapp_webhook_secret",
            "whatsapp_connection_panel",
        )}),
        ("PIX (pagamento de eventos)", {"fields": (
            "pix_key", "pix_key_type", "pix_receiver_name", "pix_receiver_city",
        )}),
        ("Mercado Pago (opcional — confirmação automática)", {"fields": ("mercadopago_access_token",)}),
    )

    def get_urls(self):
        custom = [
            path(
                "<int:pk>/whatsapp/criar-instancia/",
                self.admin_site.admin_view(self._criar_instancia),
                name="core_church_whatsapp_criar_instancia",
            ),
            path(
                "<int:pk>/whatsapp/qrcode/",
                self.admin_site.admin_view(self._ver_qrcode),
                name="core_church_whatsapp_qrcode",
            ),
            path(
                "<int:pk>/whatsapp/status/",
                self.admin_site.admin_view(self._ver_status),
                name="core_church_whatsapp_status",
            ),
            path(
                "<int:pk>/whatsapp/desconectar/",
                self.admin_site.admin_view(self._desconectar),
                name="core_church_whatsapp_desconectar",
            ),
        ]
        return custom + super().get_urls()

    def _back_to_change_form(self, pk):
        return redirect(reverse("admin:core_church_change", args=[pk]))

    def _criar_instancia(self, request, pk):
        """Cria a instância desta igreja no servidor Evolution API
        compartilhado, usando a chave GLOBAL da plataforma
        (`settings.EVOLUTION_API_KEY`) — precisa dela e de
        `settings.EVOLUTION_API_URL` configuradas no `.env`."""
        church = get_object_or_404(Church, pk=pk)
        if not (church.whatsapp_api_url and church.whatsapp_api_key):
            messages.error(request, "Configure EVOLUTION_API_URL/EVOLUTION_API_KEY no .env da plataforma antes de criar.")
            return self._back_to_change_form(pk)

        # Gera o segredo do webhook agora, se a igreja ainda não tiver um —
        # assim toda igreja nova já sai com confirmação de entrega
        # funcionando, sem precisar de um passo manual depois (era o caso
        # antes: o campo existia, mas nada configurava o webhook de
        # verdade na Evolution API).
        if not church.whatsapp_webhook_secret:
            church.whatsapp_webhook_secret = secrets.token_hex(16)
            church.save(update_fields=["whatsapp_webhook_secret"])
        webhook_url = request.build_absolute_uri(reverse("notifications:webhook"))

        try:
            data = whatsapp.criar_instancia(
                church, instance_name=church.whatsapp_instance,
                webhook_url=webhook_url, webhook_secret=church.whatsapp_webhook_secret,
            )
        except Exception as exc:
            messages.error(request, f"Falha ao criar instância: {exc}")
            return self._back_to_change_form(pk)

        # Formato de resposta confirmado ao vivo contra um servidor
        # Evolution v2.3.7 real — `hash` vem como string direto (não
        # aninhado); o fallback pros outros formatos fica só por segurança
        # contra versões diferentes do servidor.
        token = (
            data.get("hash", {}).get("apikey")
            if isinstance(data.get("hash"), dict)
            else data.get("hash") or data.get("apikey") or ""
        )
        if token:
            church.whatsapp_instance_token = token
            church.save(update_fields=["whatsapp_instance_token"])
            messages.success(request, "Instância criada e chave salva. Agora escaneie o QR code.")
        else:
            messages.warning(
                request,
                "Instância criada, mas não achei a chave na resposta — confira o formato retornado "
                "pelo seu servidor e preencha 'Chave da instância' manualmente se necessário.",
            )
        return self._back_to_change_form(pk)

    def _ver_qrcode(self, request, pk):
        church = get_object_or_404(Church, pk=pk)
        if not church.whatsapp_api_configured:
            messages.error(request, "Configure EVOLUTION_API_URL/EVOLUTION_API_KEY e crie a instância antes de pedir o QR code.")
            return self._back_to_change_form(pk)
        try:
            data = whatsapp.obter_qrcode(church)
        except Exception as exc:
            messages.error(request, f"Falha ao obter QR code: {exc}")
            return self._back_to_change_form(pk)

        qr_base64 = data.get("base64") or data.get("qrcode", {}).get("base64", "")
        if not qr_base64:
            messages.warning(request, f"Não achei o QR code no formato esperado. Resposta crua: {data}")
            return self._back_to_change_form(pk)
        img_src = qr_base64 if qr_base64.startswith("data:") else f"data:image/png;base64,{qr_base64}"
        return HttpResponse(
            f'<body style="text-align:center;font-family:sans-serif;padding:2rem;">'
            f'<h3>Escaneie com o WhatsApp do número de {church.name}</h3>'
            f'<img src="{img_src}" style="width:280px;height:280px;">'
            f'<p><a href="{reverse("admin:core_church_change", args=[pk])}">← Voltar</a></p>'
            f"</body>"
        )

    def _ver_status(self, request, pk):
        church = get_object_or_404(Church, pk=pk)
        if not church.whatsapp_api_configured:
            messages.error(request, "Configure e crie a instância antes de checar o status.")
            return self._back_to_change_form(pk)
        try:
            data = whatsapp.obter_status_conexao(church)
        except Exception as exc:
            messages.error(request, f"Falha ao checar status: {exc}")
            return self._back_to_change_form(pk)
        estado = data.get("state") or data.get("instance", {}).get("state") or str(data)
        messages.info(request, f"Status da instância '{church.whatsapp_instance}': {estado}")
        return self._back_to_change_form(pk)

    def _desconectar(self, request, pk):
        """Logout da instância — não apaga nada, só desconecta o número.
        Simetria com o botão "Desconectar" que a igreja vê na tela in-app
        (`notifications.WhatsAppDisconnectView`), pro dono poder fazer o
        mesmo direto pelo admin se precisar."""
        church = get_object_or_404(Church, pk=pk)
        try:
            whatsapp.desconectar_instancia(church)
            messages.success(request, "Instância desconectada.")
        except Exception as exc:
            messages.error(request, f"Falha ao desconectar: {exc}")
        return self._back_to_change_form(pk)

    @admin.display(description="Conexão WhatsApp")
    def whatsapp_connection_panel(self, obj):
        if not obj or not obj.pk:
            return "Salve a igreja primeiro."
        return format_html(
            '<a class="button" href="{}">Criar/recriar instância</a>&nbsp;'
            '<a class="button" href="{}" target="_blank">Ver QR code</a>&nbsp;'
            '<a class="button" href="{}">Checar status</a>&nbsp;'
            '<a class="button" href="{}">Desconectar</a>'
            '<p style="margin-top:.5rem;color:#666;font-size:.85rem;">'
            "URL/chave global do servidor Evolution vêm do .env da plataforma "
            "(EVOLUTION_API_URL/EVOLUTION_API_KEY) — não são por igreja. Clique em "
            "\"Criar/recriar instância\" pra provisionar esta igreja nele, depois "
            "\"Ver QR code\" pra conectar o número. A tela que a igreja usa no dia a "
            "dia (Conectar/Desconectar simplificado) fica em Mensagens → Conectar "
            "WhatsApp, dentro do sistema."
            "</p>",
            reverse("admin:core_church_whatsapp_criar_instancia", args=[obj.pk]),
            reverse("admin:core_church_whatsapp_qrcode", args=[obj.pk]),
            reverse("admin:core_church_whatsapp_status", args=[obj.pk]),
            reverse("admin:core_church_whatsapp_desconectar", args=[obj.pk]),
        )
