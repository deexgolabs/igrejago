import requests
from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.html import format_html

from core import whatsapp
from notifications.models import MessageTemplate, WhatsAppInstance, WhatsAppMessage, WhatsAppMetaTemplate


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    list_display = (
        "phone", "person", "status", "delivery_status", "retry_count",
        "scheduled_for", "sent_at", "campaign_label",
    )
    list_filter = ("status", "delivery_status", "campaign_label")
    search_fields = ("phone", "person__full_name", "message")
    date_hierarchy = "created_at"
    readonly_fields = (
        "sent_at", "error_message", "created_by", "created_at",
        "external_id", "delivered_at", "read_at",
    )


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "created_at")
    search_fields = ("name", "body")


@admin.register(WhatsAppMetaTemplate)
class WhatsAppMetaTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "language", "status", "meta_template_id", "submitted_at")
    list_filter = ("status", "category")
    search_fields = ("name", "meta_template_id")
    readonly_fields = ("meta_template_id", "submitted_at", "status_checked_at", "created_at")


@admin.register(WhatsAppInstance)
class WhatsAppInstanceAdmin(admin.ModelAdmin):
    """Provisionamento no servidor Evolution API compartilhado — igual
    fazia `core.admin.ChurchConfigAdmin` antes de uma igreja poder ter
    mais de um número; agora é uma linha por INSTÂNCIA, não por igreja."""

    list_display = ("name", "church", "whatsapp_instance", "is_default", "created_at")
    list_filter = ("is_default",)
    search_fields = ("name", "church__name", "whatsapp_instance")
    readonly_fields = ("whatsapp_instance", "whatsapp_instance_token", "webhook_secret", "whatsapp_connection_panel")
    fieldsets = (
        ("Instância", {"fields": ("church", "name", "is_default")}),
        ("Ritmo de envio", {"fields": ("send_interval_seconds", "batch_size", "max_retries")}),
        ("Conexão (servidor Evolution compartilhado)", {"fields": (
            "whatsapp_instance", "whatsapp_instance_token", "webhook_secret", "whatsapp_connection_panel",
        )}),
    )

    def get_urls(self):
        custom = [
            path(
                "<int:pk>/whatsapp/criar-instancia/",
                self.admin_site.admin_view(self._criar_instancia),
                name="notifications_whatsappinstance_criar_instancia",
            ),
            path(
                "<int:pk>/whatsapp/qrcode/",
                self.admin_site.admin_view(self._ver_qrcode),
                name="notifications_whatsappinstance_qrcode",
            ),
            path(
                "<int:pk>/whatsapp/status/",
                self.admin_site.admin_view(self._ver_status),
                name="notifications_whatsappinstance_status",
            ),
            path(
                "<int:pk>/whatsapp/desconectar/",
                self.admin_site.admin_view(self._desconectar),
                name="notifications_whatsappinstance_desconectar",
            ),
        ]
        return custom + super().get_urls()

    def _back_to_change_form(self, pk):
        return redirect(reverse("admin:notifications_whatsappinstance_change", args=[pk]))

    def _criar_instancia(self, request, pk):
        instance = get_object_or_404(WhatsAppInstance, pk=pk)
        if not (instance.whatsapp_api_url and instance.whatsapp_api_key):
            messages.error(request, "Configure EVOLUTION_API_URL/EVOLUTION_API_KEY no .env da plataforma antes de criar.")
            return self._back_to_change_form(pk)

        webhook_url = request.build_absolute_uri(reverse("notifications:webhook"))
        try:
            data = whatsapp.criar_instancia(
                instance, instance_name=instance.whatsapp_instance,
                webhook_url=webhook_url, webhook_secret=instance.webhook_secret,
            )
        except requests.HTTPError as exc:
            # A Evolution API rejeita (403 "already in use") recriar uma
            # instância com o mesmo nome — inofensivo pra quem só queria
            # (re)configurar o webhook, sem afetar a conexão já feita.
            resposta = exc.response.text if exc.response is not None else ""
            if exc.response is not None and exc.response.status_code == 403 and "already in use" in resposta:
                try:
                    whatsapp.configurar_webhook(
                        instance, instance_name=instance.whatsapp_instance,
                        webhook_url=webhook_url, webhook_secret=instance.webhook_secret,
                    )
                    messages.success(
                        request,
                        "Essa instância já existia (conexão preservada) — webhook de confirmação "
                        "de entrega configurado/atualizado.",
                    )
                except Exception as exc2:
                    messages.error(request, f"Instância já existia, mas falhou ao configurar o webhook: {exc2}")
                return self._back_to_change_form(pk)
            messages.error(request, f"Falha ao criar instância: {exc}")
            return self._back_to_change_form(pk)
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
            instance.whatsapp_instance_token = token
            instance.save(update_fields=["whatsapp_instance_token"])
            messages.success(request, "Instância criada e chave salva. Agora escaneie o QR code.")
        else:
            messages.warning(
                request,
                "Instância criada, mas não achei a chave na resposta — confira o formato retornado "
                "pelo seu servidor e preencha 'Chave da instância' manualmente se necessário.",
            )
        return self._back_to_change_form(pk)

    def _ver_qrcode(self, request, pk):
        from django.http import HttpResponse

        instance = get_object_or_404(WhatsAppInstance, pk=pk)
        if not instance.esta_configurada:
            messages.error(request, "Configure EVOLUTION_API_URL/EVOLUTION_API_KEY e crie a instância antes de pedir o QR code.")
            return self._back_to_change_form(pk)
        try:
            data = whatsapp.obter_qrcode(instance)
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
            f'<h3>Escaneie com o WhatsApp de "{instance.name}" ({instance.church.name})</h3>'
            f'<img src="{img_src}" style="width:280px;height:280px;">'
            f'<p><a href="{reverse("admin:notifications_whatsappinstance_change", args=[pk])}">← Voltar</a></p>'
            f"</body>"
        )

    def _ver_status(self, request, pk):
        instance = get_object_or_404(WhatsAppInstance, pk=pk)
        if not instance.esta_configurada:
            messages.error(request, "Configure e crie a instância antes de checar o status.")
            return self._back_to_change_form(pk)
        try:
            data = whatsapp.obter_status_conexao(instance)
        except Exception as exc:
            messages.error(request, f"Falha ao checar status: {exc}")
            return self._back_to_change_form(pk)
        estado = data.get("state") or data.get("instance", {}).get("state") or str(data)
        messages.info(request, f"Status da instância '{instance.whatsapp_instance}': {estado}")
        return self._back_to_change_form(pk)

    def _desconectar(self, request, pk):
        instance = get_object_or_404(WhatsAppInstance, pk=pk)
        try:
            whatsapp.desconectar_instancia(instance)
            messages.success(request, "Instância desconectada.")
        except Exception as exc:
            messages.error(request, f"Falha ao desconectar: {exc}")
        return self._back_to_change_form(pk)

    @admin.display(description="Conexão WhatsApp")
    def whatsapp_connection_panel(self, obj):
        if not obj or not obj.pk:
            return "Salve a instância primeiro."
        return format_html(
            '<a class="button" href="{}">Criar/recriar instância</a>&nbsp;'
            '<a class="button" href="{}" target="_blank">Ver QR code</a>&nbsp;'
            '<a class="button" href="{}">Checar status</a>&nbsp;'
            '<a class="button" href="{}">Desconectar</a>'
            '<p style="margin-top:.5rem;color:#666;font-size:.85rem;">'
            "URL/chave global do servidor Evolution vêm do .env da plataforma "
            "(EVOLUTION_API_URL/EVOLUTION_API_KEY) — não são por instância. Clique em "
            "\"Criar/recriar instância\" pra provisionar esta no servidor, depois "
            "\"Ver QR code\" pra conectar o número. A tela que a igreja usa no dia a "
            "dia fica em Mensagens → WhatsApp, dentro do sistema."
            "</p>",
            reverse("admin:notifications_whatsappinstance_criar_instancia", args=[obj.pk]),
            reverse("admin:notifications_whatsappinstance_qrcode", args=[obj.pk]),
            reverse("admin:notifications_whatsappinstance_status", args=[obj.pk]),
            reverse("admin:notifications_whatsappinstance_desconectar", args=[obj.pk]),
        )
