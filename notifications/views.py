import json
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from accounts.mixins import CanManagePeopleMixin, IsChurchManagerMixin
from core import whatsapp
from core.billing import whatsapp_liberado
from core.models import Church
from core.tenancy import TenantFormMixin
from core.views import enviar_email_confirmacao
from notifications.forms import MessageTemplateForm, ScheduledMessageForm, WhatsAppMetaTemplateForm, WhatsAppProviderForm
from notifications.models import (
    EmailMessage,
    MessageTemplate,
    PushSubscription,
    SMSMessage,
    WhatsAppMessage,
    WhatsAppMetaTemplate,
)

logger = logging.getLogger(__name__)


def normalize_phone(raw):
    """Mesma normalização de `people.Person.whatsapp_number` — dígitos só,
    com DDI 55 se faltar — mas standalone, pra funcionar também com um
    telefone digitado direto (sem Person por trás)."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


class ScheduledMessageCreateView(CanManagePeopleMixin, View):
    template_name = "notifications/scheduled_message_form.html"

    def get(self, request):
        return render(request, self.template_name, {
            "form": ScheduledMessageForm(user=request.user), "templates": MessageTemplate.objects.all(),
        })

    def post(self, request):
        form = ScheduledMessageForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {
                "form": form, "templates": MessageTemplate.objects.all(),
            })

        WhatsAppMessage.objects.create(
            church=request.church,
            person=form.cleaned_data.get("person"),
            phone=normalize_phone(form.cleaned_data["phone"]),
            message=form.cleaned_data["message"],
            scheduled_for=form.cleaned_data.get("scheduled_for"),
            campaign_label="Mensagem avulsa",
            created_by=request.user,
        )
        messages.success(request, "Mensagem adicionada à fila.")
        return redirect("notifications:queue")


# Cadência real do processamento da fila — a tarefa "sempre ativa" no
# PythonAnywhere chama `processar_fila_whatsapp` e dorme esse tanto entre
# uma chamada e outra (ver o comando de `always_on` configurado no painel
# da PythonAnywhere). Usado só pra dar uma ESTIMATIVA de horário na tela
# da fila — se esse intervalo mudar na infraestrutura, atualize aqui
# também pra não mostrar um horário impreciso.
WHATSAPP_QUEUE_CYCLE_SECONDS = 55


class MessageQueueListView(CanManagePeopleMixin, ListView):
    model = WhatsAppMessage
    template_name = "notifications/queue_list.html"
    context_object_name = "queue_messages"
    paginate_by = 50

    def get_queryset(self):
        qs = WhatsAppMessage.objects.select_related("person").order_by("-created_at")
        user = self.request.user
        if not user.is_unrestricted_manager:
            # Líder de Departamento escopado só vê mensagens de pessoas do
            # próprio departamento — mensagem avulsa por telefone (sem
            # `person`) fica invisível pra ele, só Pastor/Secretaria vê a
            # fila completa.
            qs = qs.filter(person__department__in=user.led_departments)
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_choices"] = WhatsAppMessage.Status.choices
        context["current_status"] = self.request.GET.get("status", "")
        self._anotar_previsao_de_envio(context["queue_messages"])
        return context

    def _anotar_previsao_de_envio(self, page_messages):
        """Calcula, pra cada mensagem PENDING/FAILED elegível AGORA, a
        mesma posição que `processar_fila_whatsapp` usaria (mesmíssima
        query, `order_by("created_at")`) e estima o horário de
        processamento a partir daí — isso é o que aparece na coluna
        "Previsão de envio" no lugar do genérico "assim que possível".
        Sem isso, ninguém da secretaria conseguia saber quando uma
        mensagem ia sair de verdade (achado num relato real de usuário)."""
        from django.db.models import Q

        church = self.request.church
        now = timezone.now()
        eligible_ids = list(
            WhatsAppMessage.objects.filter(
                Q(status=WhatsAppMessage.Status.PENDING)
                & (Q(scheduled_for__isnull=True) | Q(scheduled_for__lte=now))
                | Q(status=WhatsAppMessage.Status.FAILED, retry_count__lt=church.whatsapp_max_retries)
            )
            .order_by("created_at")
            .values_list("pk", flat=True)
        )
        positions = {pk: i for i, pk in enumerate(eligible_ids)}
        batch_size = church.whatsapp_batch_size or 1

        for msg in page_messages:
            position = positions.get(msg.pk)
            if position is None:
                msg.queue_position = None
                msg.estimated_send_at = None
                continue
            runs_ahead, offset_in_batch = divmod(position, batch_size)
            msg.queue_position = position + 1
            msg.estimated_send_at = now + timedelta(
                seconds=runs_ahead * WHATSAPP_QUEUE_CYCLE_SECONDS
                + offset_in_batch * church.whatsapp_send_interval_seconds
            )


def _mensagens_escopadas(user):
    """`WhatsAppMessage.objects` (todas) pra Pastor/Secretaria; só as de
    pessoas do(s) departamento(s) liderado(s) pra um Líder de Departamento
    escopado — mesmo princípio de `MessageQueueListView.get_queryset`,
    reaproveitado aqui pra impedir cancelar/reenviar mensagem de outro
    departamento direto pela URL."""
    if user.is_unrestricted_manager:
        return WhatsAppMessage.objects.all()
    return WhatsAppMessage.objects.filter(person__department__in=user.led_departments)


class MessageCancelView(CanManagePeopleMixin, View):
    def post(self, request, pk):
        message = get_object_or_404(
            _mensagens_escopadas(request.user), pk=pk, status=WhatsAppMessage.Status.PENDING
        )
        message.status = WhatsAppMessage.Status.CANCELLED
        message.save(update_fields=["status"])
        messages.success(request, "Mensagem cancelada.")
        return redirect("notifications:queue")


class MessageResendView(CanManagePeopleMixin, View):
    """Reenvio manual de uma mensagem FAILED — zera as tentativas
    automáticas também, já que a pessoa está pedindo explicitamente pra
    tentar de novo (não deveria contar contra `whatsapp_max_retries`)."""

    def post(self, request, pk):
        message = get_object_or_404(
            _mensagens_escopadas(request.user), pk=pk, status=WhatsAppMessage.Status.FAILED
        )
        message.status = WhatsAppMessage.Status.PENDING
        message.retry_count = 0
        message.error_message = ""
        message.save(update_fields=["status", "retry_count", "error_message"])
        messages.success(request, "Mensagem voltou pra fila — será reenviada no próximo processamento.")
        return redirect("notifications:queue")


class MessageTemplateListView(IsChurchManagerMixin, ListView):
    model = MessageTemplate
    template_name = "notifications/template_list.html"
    context_object_name = "message_templates"


class MessageTemplateCreateView(TenantFormMixin, IsChurchManagerMixin, CreateView):
    model = MessageTemplate
    form_class = MessageTemplateForm
    template_name = "notifications/template_form.html"
    success_url = "/mensagens/modelos/"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, "Modelo criado.")
        return super().form_valid(form)


class MessageTemplateUpdateView(IsChurchManagerMixin, UpdateView):
    model = MessageTemplate
    form_class = MessageTemplateForm
    template_name = "notifications/template_form.html"
    success_url = "/mensagens/modelos/"

    def form_valid(self, form):
        messages.success(self.request, "Modelo atualizado.")
        return super().form_valid(form)


class MessageTemplateDeleteView(IsChurchManagerMixin, DeleteView):
    model = MessageTemplate
    template_name = "notifications/template_confirm_delete.html"
    success_url = "/mensagens/modelos/"

    def form_valid(self, form):
        messages.success(self.request, "Modelo removido.")
        return super().form_valid(form)


def _connection_context(request, **extra):
    """Estado da tela de Conectar/Desconectar — a igreja nunca vê URL,
    chave ou nome de instância da Evolution aqui (isso é config do
    dono, só no Django admin); só se está configurado, conectado ou
    não, e o QR code quando acabou de ser gerado. Provider-aware desde
    a Meta Cloud API: Evolution tem um passo de "conectar" de verdade
    (QR/pareamento), checado ao vivo; Meta não tem — lá "conectado" é
    só "as credenciais estão preenchidas", sem chamada extra."""
    config = request.church
    connected = None
    if config.whatsapp_provider == Church.WhatsAppProvider.META_CLOUD:
        connected = True if config.whatsapp_api_configured else None
    elif config.whatsapp_api_configured:
        try:
            data = whatsapp.obter_status_conexao(config)
            estado = data.get("state") or data.get("instance", {}).get("state") or ""
            connected = estado == "open"
        except Exception:
            connected = None  # não deu pra saber — trata como "desconhecido", não como erro na tela
    context = {
        "configured": config.whatsapp_api_configured, "connected": connected,
        "email_confirmed": config.email_confirmed,
        "whatsapp_liberado": whatsapp_liberado(config),
        "provider": config.whatsapp_provider,
        "is_meta_cloud": config.whatsapp_provider == Church.WhatsAppProvider.META_CLOUD,
        "provider_form": WhatsAppProviderForm(instance=config),
    }
    context.update(extra)
    return context


class PushSubscribeView(LoginRequiredMixin, View):
    """Recebe a inscrição de push gerada no navegador
    (`PushManager.subscribe()`, ver `templates/base.html`) e salva —
    qualquer usuário logado pode se inscrever, não só quem gerencia
    pessoas. `update_or_create` por `endpoint` porque o mesmo navegador
    pode re-inscrever (ex.: depois de limpar dados) com o mesmo endpoint."""

    def post(self, request):
        try:
            data = json.loads(request.body)
            endpoint = data["endpoint"]
            p256dh = data["keys"]["p256dh"]
            auth = data["keys"]["auth"]
        except (KeyError, ValueError, TypeError):
            return HttpResponseBadRequest("payload inválido")

        PushSubscription.objects.update_or_create(
            church=request.church, endpoint=endpoint,
            defaults={"user": request.user, "p256dh": p256dh, "auth": auth},
        )
        return HttpResponse(status=204)


class WhatsAppConnectionView(IsChurchManagerMixin, View):
    """A tela onde o pastor/secretaria conecta o número de WhatsApp da
    igreja — só "Conectar" (que já mostra o QR code na hora) e
    "Desconectar", sem nenhum campo técnico. Isso é infraestrutura
    configurada pelo dono do sistema via Django admin
    (`core/admin.py::ChurchConfigAdmin`)."""

    template_name = "notifications/whatsapp_connection.html"

    def get(self, request):
        return render(request, self.template_name, _connection_context(request))


class WhatsAppConnectView(IsChurchManagerMixin, View):
    """Botão único "Conectar": tenta pegar o QR code direto (instância já
    existe na maioria dos casos, criada uma vez pelo dono); se falhar
    (primeira vez, instância ainda não existe), cria a instância e tenta
    de novo. Renderiza o QR code na própria tela em vez de redirecionar
    pra uma página separada, já que ele expira em minutos e a igreja só
    precisa desse fluxo simples."""

    def post(self, request):
        config = request.church
        if config.whatsapp_provider == Church.WhatsAppProvider.META_CLOUD:
            messages.error(request, "O canal atual é a API oficial da Meta — não tem QR code, só preencher as credenciais acima.")
            return render(request, WhatsAppConnectionView.template_name, _connection_context(request))
        if not config.email_confirmed:
            messages.error(
                request,
                "Confirme o e-mail da igreja (link enviado no cadastro) antes de conectar o WhatsApp.",
            )
            return render(request, WhatsAppConnectionView.template_name, _connection_context(request))
        if not whatsapp_liberado(config):
            messages.error(
                request,
                "O envio de WhatsApp não está incluído no seu plano atual — assine o plano Pro em Configurações → Assinatura.",
            )
            return render(request, WhatsAppConnectionView.template_name, _connection_context(request))
        if not config.whatsapp_api_configured:
            messages.error(
                request,
                "A conexão do WhatsApp ainda não foi configurada pelo responsável técnico do sistema.",
            )
            return render(request, WhatsAppConnectionView.template_name, _connection_context(request))

        try:
            data = whatsapp.obter_qrcode(config)
        except Exception:
            try:
                whatsapp.criar_instancia(config, instance_name=config.whatsapp_instance)
                data = whatsapp.obter_qrcode(config)
            except Exception as exc:
                messages.error(request, f"Não consegui gerar o QR code: {exc}")
                return render(request, WhatsAppConnectionView.template_name, _connection_context(request))

        qr_base64 = data.get("base64") or data.get("qrcode", {}).get("base64", "")
        if not qr_base64:
            messages.warning(request, "Não recebi um QR code válido — tente novamente em alguns segundos.")
            return render(request, WhatsAppConnectionView.template_name, _connection_context(request))

        img_src = qr_base64 if qr_base64.startswith("data:") else f"data:image/png;base64,{qr_base64}"
        return render(
            request, WhatsAppConnectionView.template_name, _connection_context(request, img_src=img_src)
        )


class WhatsAppMetaConfigView(IsChurchManagerMixin, View):
    """Salva a escolha de canal (Evolution × API oficial da Meta) +
    credenciais da Meta, direto na tela de Conectar WhatsApp — trocar
    pra Evolution não apaga o que já estava preenchido da Meta (e
    vice-versa), só muda qual delas `Church.whatsapp_api_configured`/
    `core.whatsapp.enviar_whatsapp` usam pra valer."""

    def post(self, request):
        form = WhatsAppProviderForm(request.POST, instance=request.church)
        if form.is_valid():
            form.save()
            messages.success(request, "Canal de WhatsApp atualizado.")
        else:
            messages.error(request, "Não deu pra salvar — confira os campos.")
        return redirect("notifications:whatsapp_connection")


class WhatsAppMetaTemplateListView(IsChurchManagerMixin, ListView):
    model = WhatsAppMetaTemplate
    template_name = "notifications/whatsapp_meta_template_list.html"
    context_object_name = "meta_templates"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["templates_configured"] = self.request.church.whatsapp_meta_templates_configured
        return context


class WhatsAppMetaTemplateCreateView(TenantFormMixin, IsChurchManagerMixin, CreateView):
    model = WhatsAppMetaTemplate
    form_class = WhatsAppMetaTemplateForm
    template_name = "notifications/whatsapp_meta_template_form.html"
    success_url = "/mensagens/whatsapp/templates/"

    def form_valid(self, form):
        messages.success(self.request, "Template criado como rascunho.")
        return super().form_valid(form)


class WhatsAppMetaTemplateUpdateView(IsChurchManagerMixin, UpdateView):
    form_class = WhatsAppMetaTemplateForm
    template_name = "notifications/whatsapp_meta_template_form.html"
    success_url = "/mensagens/whatsapp/templates/"

    def get_queryset(self):
        # Só dá pra editar em DRAFT/REJECTED — a Meta não permite mudar
        # um template em análise/aprovado. Tentar editar um PENDING/
        # APPROVED por URL direta cai fora do queryset e vira 404, mesmo
        # padrão de escopo já usado no projeto (ex.: líder de departamento).
        return WhatsAppMetaTemplate.objects.filter(
            status__in=[WhatsAppMetaTemplate.Status.DRAFT, WhatsAppMetaTemplate.Status.REJECTED]
        )

    def form_valid(self, form):
        messages.success(self.request, "Template atualizado.")
        return super().form_valid(form)


class WhatsAppMetaTemplateDeleteView(IsChurchManagerMixin, DeleteView):
    model = WhatsAppMetaTemplate
    template_name = "notifications/whatsapp_meta_template_confirm_delete.html"
    success_url = "/mensagens/whatsapp/templates/"

    def form_valid(self, form):
        template = self.object
        config = self.request.church
        if template.meta_template_id and config.whatsapp_meta_templates_configured:
            # Best-effort: exclui na Meta também, mas o registro local
            # some de qualquer jeito mesmo se a chamada externa falhar
            # (nunca travar uma exclusão local numa API de terceiro fora
            # do ar/token expirado).
            try:
                whatsapp.excluir_template_meta(
                    access_token=config.whatsapp_meta_access_token,
                    waba_id=config.whatsapp_meta_business_account_id,
                    name=template.name,
                )
            except Exception:
                logger.exception("Falha ao excluir template %s na Meta", template.name)
        messages.success(self.request, "Template removido.")
        return super().form_valid(form)


class WhatsAppMetaTemplateSubmitView(IsChurchManagerMixin, View):
    """Envia o template pra revisão de verdade na Meta — só a partir de
    DRAFT/REJECTED. Sucesso grava o id devolvido e vira PENDING; erro
    HTTP mostra a mensagem da própria Meta (mesmo padrão de
    `_enviar_via_meta_cloud`), sem mudar o status local."""

    def post(self, request, pk):
        template = get_object_or_404(
            WhatsAppMetaTemplate,
            pk=pk,
            status__in=[WhatsAppMetaTemplate.Status.DRAFT, WhatsAppMetaTemplate.Status.REJECTED],
        )
        config = request.church
        if not config.whatsapp_meta_templates_configured:
            messages.error(request, "Preencha o WhatsApp Business Account ID e o Access Token acima antes de enviar.")
            return redirect("notifications:whatsapp_meta_templates")

        try:
            data = whatsapp.criar_template_meta(
                waba_id=config.whatsapp_meta_business_account_id,
                access_token=config.whatsapp_meta_access_token,
                name=template.name,
                language=template.language,
                category=template.category,
                components=template.montar_components(),
            )
        except Exception as exc:
            detalhe = str(exc)
            try:
                detalhe = exc.response.json().get("error", {}).get("message", detalhe)
            except Exception:
                pass
            messages.error(request, f"A Meta recusou o template: {detalhe}")
            return redirect("notifications:whatsapp_meta_templates")

        template.meta_template_id = data.get("id", "")
        template.status = WhatsAppMetaTemplate.Status.PENDING
        template.submitted_at = timezone.now()
        template.save(update_fields=["meta_template_id", "status", "submitted_at"])
        messages.success(request, "Template enviado pra aprovação da Meta.")
        return redirect("notifications:whatsapp_meta_templates")


class WhatsAppMetaTemplateRefreshStatusView(IsChurchManagerMixin, View):
    """Consulta o status real na Meta — nunca automático (não há webhook
    de status configurado), sempre por clique explícito. Mesmo princípio
    de "nunca confiar em cache, sempre reconsultar" de
    `RecurringPledgeMercadoPagoWebhookView._processar_preapproval`."""

    _STATUS_MAP = {
        "APPROVED": WhatsAppMetaTemplate.Status.APPROVED,
        "REJECTED": WhatsAppMetaTemplate.Status.REJECTED,
        "PENDING": WhatsAppMetaTemplate.Status.PENDING,
        "IN_REVIEW": WhatsAppMetaTemplate.Status.PENDING,
        "PAUSED": WhatsAppMetaTemplate.Status.DISABLED,
        "DISABLED": WhatsAppMetaTemplate.Status.DISABLED,
    }

    def post(self, request, pk):
        template = get_object_or_404(WhatsAppMetaTemplate, pk=pk)
        config = request.church
        if not template.meta_template_id or not config.whatsapp_meta_templates_configured:
            messages.error(request, "Este template ainda não foi enviado pra Meta.")
            return redirect("notifications:whatsapp_meta_templates")

        try:
            data = whatsapp.consultar_status_template_meta(
                access_token=config.whatsapp_meta_access_token, template_id=template.meta_template_id,
            )
        except Exception:
            logger.exception("Falha ao consultar status do template %s na Meta", template.pk)
            messages.error(request, "Não deu pra consultar o status agora — tente de novo em instantes.")
            return redirect("notifications:whatsapp_meta_templates")

        novo_status = self._STATUS_MAP.get(data.get("status", ""), template.status)
        template.status = novo_status
        template.rejection_reason = data.get("rejected_reason", "") or ""
        template.status_checked_at = timezone.now()
        template.save(update_fields=["status", "rejection_reason", "status_checked_at"])
        messages.success(request, f"Status atualizado: {template.get_status_display()}.")
        return redirect("notifications:whatsapp_meta_templates")


class ResendConfirmationEmailView(IsChurchManagerMixin, View):
    """Reenvia o e-mail de confirmação da igreja (Fase 2) — botão que
    aparece na própria tela de Conectar WhatsApp enquanto
    `Church.email_confirmed` for falso."""

    def post(self, request):
        if request.church.email_confirmed:
            messages.info(request, "O e-mail já está confirmado.")
        elif not request.user.email:
            # Sem isso, `enviar_email_confirmacao` falharia calada (ela
            # engole exceção de propósito, pra nunca derrubar quem chama)
            # e a mensagem de sucesso abaixo seria um falso positivo —
            # ninguém recebe nada, mas a tela diz que reenviou.
            messages.error(request, "Sua conta não tem e-mail cadastrado — peça pro dono atualizar em Configurações.")
        else:
            enviar_email_confirmacao(
                request, request.church, request.user.email,
                request.user.first_name or request.user.username,
            )
            messages.success(request, "E-mail de confirmação reenviado.")
        return redirect("notifications:whatsapp_connection")


class WhatsAppDisconnectView(IsChurchManagerMixin, View):
    def post(self, request):
        config = request.church
        try:
            whatsapp.desconectar_instancia(config)
            messages.success(request, "WhatsApp desconectado.")
        except Exception as exc:
            messages.error(request, f"Falha ao desconectar: {exc}")
        return redirect("notifications:whatsapp_connection")


_DELIVERY_STATUS_MAP = {
    # Baileys/Evolution usam tanto o código numérico quanto o nome — aceita
    # os dois. Nunca confirmado contra um payload real (nenhum servidor
    # Evolution existe neste ambiente de dev); ajuste aqui se o seu
    # servidor mandar um formato diferente.
    "3": WhatsAppMessage.DeliveryStatus.DELIVERED,
    "DELIVERY_ACK": WhatsAppMessage.DeliveryStatus.DELIVERED,
    "4": WhatsAppMessage.DeliveryStatus.READ,
    "READ": WhatsAppMessage.DeliveryStatus.READ,
    "5": WhatsAppMessage.DeliveryStatus.READ,
    "PLAYED": WhatsAppMessage.DeliveryStatus.READ,
    "0": WhatsAppMessage.DeliveryStatus.FAILED,
    "-1": WhatsAppMessage.DeliveryStatus.FAILED,
    "ERROR": WhatsAppMessage.DeliveryStatus.FAILED,
}


@method_decorator(csrf_exempt, name="dispatch")
class WhatsAppWebhookView(View):
    """Recebe o evento `messages.update` da Evolution API (confirmação de
    entrega/leitura) e atualiza o `WhatsAppMessage` correspondente pelo
    `external_id`. Servidor Evolution único, uma instância por igreja — o
    cabeçalho `X-Webhook-Secret` (batendo com `Church.whatsapp_webhook_secret`)
    é o que diz DE QUAL igreja é o evento, já que não há usuário logado
    nem slug na URL do webhook. Sem segredo configurado ou sem igreja
    correspondente, rejeita tudo — não existe um modo "aberto" por
    padrão."""

    def post(self, request):
        secret = request.headers.get("X-Webhook-Secret")
        if not secret:
            return HttpResponseForbidden("webhook not configured")
        config = Church.objects.filter(whatsapp_webhook_secret=secret).first()
        if config is None:
            return HttpResponseForbidden("invalid secret")

        try:
            payload = json.loads(request.body)
        except Exception:
            return HttpResponse(status=400)

        data = payload.get("data", {})
        # Confirmado contra um servidor Evolution real (v2.3.7): o evento
        # `messages.update` manda `data.keyId`/`data.status` DIRETO, sem
        # aninhar em `key`/`update` — só o evento `send.message` (que não
        # nos interessa aqui) tem esse aninhamento. Aceita os dois formatos
        # mesmo assim, caso uma versão futura da Evolution volte a aninhar.
        message_id = data.get("keyId") or data.get("key", {}).get("id", "")
        raw_status = str(data.get("status") or data.get("update", {}).get("status", ""))
        if not message_id:
            return HttpResponse(status=200)  # evento sem id útil pra nós — ignora sem erro

        delivery_status = _DELIVERY_STATUS_MAP.get(raw_status)
        if delivery_status is None:
            return HttpResponse(status=200)  # status que não mapeamos (ex.: SERVER_ACK) — ignora

        updated = WhatsAppMessage.todas_as_igrejas.filter(external_id=message_id, church=config)
        now = timezone.now()
        if delivery_status == WhatsAppMessage.DeliveryStatus.DELIVERED:
            updated.update(delivery_status=delivery_status, delivered_at=now)
        elif delivery_status == WhatsAppMessage.DeliveryStatus.READ:
            updated.update(delivery_status=delivery_status, read_at=now)
        else:
            updated.update(delivery_status=delivery_status)
        return HttpResponse(status=200)


class EmailQueueListView(CanManagePeopleMixin, ListView):
    """Fila de e-mail em massa — mesmo padrão de `MessageQueueListView`,
    sem a estimativa de horário (SMTP não tem o mesmo intervalo/risco de
    banimento por rajada que o WhatsApp, então não há "posição na fila"
    real pra calcular)."""

    model = EmailMessage
    template_name = "notifications/email_queue_list.html"
    context_object_name = "queue_messages"
    paginate_by = 50

    def get_queryset(self):
        qs = EmailMessage.objects.select_related("person").order_by("-created_at")
        user = self.request.user
        if not user.is_unrestricted_manager:
            qs = qs.filter(person__department__in=user.led_departments)
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_choices"] = EmailMessage.Status.choices
        context["current_status"] = self.request.GET.get("status", "")
        return context


class EmailMessageCancelView(CanManagePeopleMixin, View):
    def post(self, request, pk):
        message = get_object_or_404(EmailMessage, pk=pk, status=EmailMessage.Status.PENDING)
        message.status = EmailMessage.Status.CANCELLED
        message.save(update_fields=["status"])
        messages.success(request, "E-mail cancelado.")
        return redirect("notifications:email_queue")


class SMSQueueListView(CanManagePeopleMixin, ListView):
    model = SMSMessage
    template_name = "notifications/sms_queue_list.html"
    context_object_name = "queue_messages"
    paginate_by = 50

    def get_queryset(self):
        qs = SMSMessage.objects.select_related("person").order_by("-created_at")
        user = self.request.user
        if not user.is_unrestricted_manager:
            qs = qs.filter(person__department__in=user.led_departments)
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_choices"] = SMSMessage.Status.choices
        context["current_status"] = self.request.GET.get("status", "")
        return context


class SMSMessageCancelView(CanManagePeopleMixin, View):
    def post(self, request, pk):
        message = get_object_or_404(SMSMessage, pk=pk, status=SMSMessage.Status.PENDING)
        message.status = SMSMessage.Status.CANCELLED
        message.save(update_fields=["status"])
        messages.success(request, "SMS cancelado.")
        return redirect("notifications:sms_queue")


# 1x1 GIF transparente (43 bytes) — o pixel de rastreio de abertura de
# e-mail devolve sempre isto, hardcoded (não tem porquê depender de
# Pillow/arquivo estático só pra 43 bytes fixos que nunca mudam).
_TRANSPARENT_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04"
    b"\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


class EmailOpenTrackingView(View):
    """Pixel de rastreio — GET público, sem login, resolvido por
    `EmailMessage.tracking_token` (mesmo espírito de
    `ConfirmarEscalaView`, resolvido por token). Devolve o GIF mesmo se
    o token não existir — nunca dar pista pra fora de que um token é
    inválido/foi adivinhado."""

    def get(self, request, token):
        EmailMessage.todas_as_igrejas.filter(tracking_token=token, opened_at__isnull=True).update(
            opened_at=timezone.now()
        )
        EmailMessage.todas_as_igrejas.filter(tracking_token=token).update(open_count=F("open_count") + 1)
        response = HttpResponse(_TRANSPARENT_GIF, content_type="image/gif")
        response["Cache-Control"] = "no-store"
        return response


class EmailClickTrackingView(View):
    """Todo link dentro de uma campanha passa por aqui antes do destino
    de verdade (`core.email_campaign._linkify_com_rastreio`) — registra
    o clique e redireciona. `?url=` só é seguido se for http(s) —
    nunca um esquema tipo `javascript:`."""

    def get(self, request, token):
        msg = get_object_or_404(EmailMessage.todas_as_igrejas, tracking_token=token)
        EmailMessage.todas_as_igrejas.filter(pk=msg.pk).update(
            click_count=F("click_count") + 1, clicked_at=msg.clicked_at or timezone.now(),
        )
        target = request.GET.get("url", "")
        if not target.startswith(("http://", "https://")):
            return redirect("core:dashboard")
        response = redirect(target)
        response["Cache-Control"] = "no-store"
        return response


class EmailUnsubscribeView(View):
    """Link "cancelar inscrição" no rodapé de todo e-mail de campanha —
    GET mostra a confirmação, POST efetiva (também é o que o cabeçalho
    `List-Unsubscribe-Post` do e-mail deixa o Gmail/Outlook chamarem
    direto, sem abrir página nenhuma — "descadastro de 1 clique")."""

    template_name = "notifications/email_unsubscribe.html"

    def get(self, request, token):
        msg = get_object_or_404(EmailMessage.todas_as_igrejas, tracking_token=token)
        return render(request, self.template_name, {"msg": msg, "done": False})

    def post(self, request, token):
        msg = get_object_or_404(EmailMessage.todas_as_igrejas, tracking_token=token)
        if msg.person_id:
            from people.models import Person
            Person.todas_as_igrejas.filter(pk=msg.person_id).update(email_opted_out_at=timezone.now())
        return render(request, self.template_name, {"msg": msg, "done": True})
