import json
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
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
from notifications.forms import MessageTemplateForm, ScheduledMessageForm
from notifications.models import EmailMessage, MessageTemplate, PushSubscription, SMSMessage, WhatsAppMessage

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
    chave ou nome de instância aqui (isso é config do dono, só no Django
    admin); só se está configurado, conectado ou não, e o QR code quando
    acabou de ser gerado."""
    config = request.church
    connected = None
    if config.whatsapp_api_configured:
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
