import csv
import logging

from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView, View

from accounts.mixins import IsChurchManagerMixin
from core.colors import generate_palette
from core.models import Church
from core.push import enviar_push_para_usuario
from core.qr import qr_data_uri
from core.ratelimit import RateLimitMixin
from core.tenancy import PublicChurchMixin, TenantFormMixin
from events import mercadopago
from events.forms import EventForm, PublicRegistrationForm
from events.models import Event, Registration
from events.pix import build_pix_payload
from notifications.models import WhatsAppMessage
from notifications.views import normalize_phone

logger = logging.getLogger(__name__)


class EventListView(IsChurchManagerMixin, ListView):
    model = Event
    template_name = "events/event_manage_list.html"
    context_object_name = "events"
    ordering = ["-start_datetime"]


class EventCreateView(TenantFormMixin, IsChurchManagerMixin, CreateView):
    model = Event
    form_class = EventForm
    template_name = "events/event_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, "Evento criado com sucesso.")
        return super().form_valid(form)


class EventUpdateView(IsChurchManagerMixin, UpdateView):
    model = Event
    form_class = EventForm
    template_name = "events/event_form.html"

    def form_valid(self, form):
        messages.success(self.request, "Evento atualizado com sucesso.")
        return super().form_valid(form)


class EventDeleteView(IsChurchManagerMixin, DeleteView):
    model = Event
    template_name = "events/event_confirm_delete.html"
    success_url = reverse_lazy("events:manage_list")

    def form_valid(self, form):
        messages.success(self.request, "Evento removido.")
        return super().form_valid(form)


class EventDetailView(PublicChurchMixin, DetailView):
    """Página pública do evento — visualização + link para inscrição.
    Quando o evento tem `brand_color` próprio, a paleta usada na página é a
    do evento, não a da igreja (`core.context_processors.church_config`
    já colocou a da igreja no contexto — aqui só sobrescreve se o evento
    definiu a sua)."""

    model = Event
    template_name = "events/event_detail.html"
    context_object_name = "event"
    slug_url_kwarg = "slug"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.object.brand_color:
            context["paleta_marca"] = generate_palette(self.object.brand_color)
        return context


class EventRegistrationView(PublicChurchMixin, RateLimitMixin, View):
    """Inscrição pública (sem login). Evento gratuito confirma na hora;
    evento pago gera o QR Code PIX e fica com status "Aguardando
    pagamento" até a secretaria confirmar manualmente (ver
    `RegistrationMarkPaidView`) — não há gateway real integrado."""

    template_name = "events/event_register.html"
    rate_limit_key = "event_register"
    rate_limit_max = 20
    rate_limit_window_seconds = 300

    def get(self, request, church_slug, slug):
        event = get_object_or_404(Event, slug=slug)
        form = PublicRegistrationForm()
        return render(request, self.template_name, {"event": event, "form": form})

    def post(self, request, church_slug, slug):
        event = get_object_or_404(Event, slug=slug)

        form = PublicRegistrationForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"event": event, "form": form})

        registration = form.save(commit=False)
        registration.event = event
        registration.church = event.church
        registration.privacy_consent_at = timezone.now()
        if request.user.is_authenticated and request.user.person_id:
            registration.person = request.user.person
        # Evento lotado não bloqueia mais a inscrição — entra na lista de
        # espera (ver `Event._confirmed_registrations`) em vez de rejeitar;
        # a secretaria promove manualmente quando abrir vaga
        # (`RegistrationPromoteView`).
        registration.on_waitlist = event.is_full
        registration.payment_status = (
            Registration.PaymentStatus.PENDING if event.is_paid else Registration.PaymentStatus.FREE
        )
        registration.save()

        if event.is_paid and not registration.on_waitlist:
            return redirect(
                "events_public:register_payment", church_slug=self.church.slug, slug=event.slug, pk=registration.pk
            )
        return redirect(
            "events_public:register_done", church_slug=self.church.slug, slug=event.slug, pk=registration.pk
        )


class RegistrationPaymentView(PublicChurchMixin, DetailView):
    """Mostra o QR Code/código PIX "copia e cola" da inscrição paga."""

    model = Registration
    template_name = "events/registration_payment.html"
    context_object_name = "registration"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["church_config"] = self.church
        registration = context["registration"]
        if self.church.pix_configured:
            payload = build_pix_payload(
                key=self.church.pix_key,
                receiver_name=self.church.pix_receiver_name,
                receiver_city=self.church.pix_receiver_city,
                amount=registration.event.price,
                txid=f"EVENTO{registration.pk}",
            )
            context["pix_payload"] = payload
            context["pix_qr_data_uri"] = self._qr_data_uri(payload)
        return context

    @staticmethod
    def _qr_data_uri(payload):
        return qr_data_uri(payload)


class MercadoPagoCheckoutStartView(PublicChurchMixin, View):
    """Cria a preferência de checkout e redireciona para o Mercado Pago.
    Só aparece como opção quando `Church.mercadopago_configured` — sem
    token configurado, a página de pagamento mostra só o PIX local."""

    def get(self, request, church_slug, slug, pk):
        registration = get_object_or_404(Registration, pk=pk, event__slug=slug)
        if not self.church.mercadopago_configured:
            messages.error(request, "Pagamento via Mercado Pago não está configurado.")
            return redirect("events_public:register_payment", church_slug=self.church.slug, slug=slug, pk=pk)

        base_url = request.build_absolute_uri("/")[:-1]
        # `church_id` vai na notification_url porque o webhook é chamado
        # pelo Mercado Pago sem usuário logado e sem navegar por slug — é
        # assim que ele sabe de qual igreja (e portanto qual access_token)
        # se trata quando reconsultar o pagamento.
        notification_url = (
            base_url + reverse("events:mercadopago_webhook") + f"?church_id={self.church.pk}"
        )
        try:
            checkout_url = mercadopago.criar_preferencia(
                access_token=self.church.mercadopago_access_token,
                registration=registration,
                back_url_success=base_url
                + reverse("events_public:register_done", args=[self.church.slug, slug, pk]),
                back_url_pending=base_url
                + reverse("events_public:register_payment", args=[self.church.slug, slug, pk]),
                notification_url=notification_url,
            )
        except Exception:
            logger.exception("Falha ao criar preferência no Mercado Pago para a inscrição %s", pk)
            messages.error(request, "Não foi possível iniciar o pagamento pelo Mercado Pago agora. Tente o PIX abaixo.")
            return redirect("events_public:register_payment", church_slug=self.church.slug, slug=slug, pk=pk)

        return redirect(checkout_url)


@method_decorator(csrf_exempt, name="dispatch")
class MercadoPagoWebhookView(View):
    """Webhook chamado pelo Mercado Pago — sem usuário logado, sem slug na
    URL. A igreja vem do `?church_id=` que NÓS embutimos na
    `notification_url` ao criar a preferência (`MercadoPagoCheckoutStartView`),
    não de nada que o Mercado Pago decida sozinho. Usa `todas_as_igrejas`
    porque não há igreja nenhuma no thread-local aqui (requisição anônima)
    — o filtro por igreja é feito explicitamente pelo `church_id`. Nunca
    confia no corpo do POST para decidir status — sempre reconsulta a API
    antes de marcar como pago."""

    def post(self, request):
        payment_id = request.GET.get("data.id") or request.GET.get("id")
        church_id = request.GET.get("church_id")
        if not payment_id or not church_id:
            return HttpResponseBadRequest("missing payment id or church_id")

        church = get_object_or_404(Church, pk=church_id)
        if not church.mercadopago_configured:
            return HttpResponseBadRequest("mercadopago not configured")

        try:
            payment = mercadopago.consultar_pagamento(
                access_token=church.mercadopago_access_token, payment_id=payment_id
            )
        except Exception:
            logger.exception("Falha ao reconsultar pagamento %s no Mercado Pago", payment_id)
            return HttpResponse(status=502)

        external_reference = payment.get("external_reference", "")
        if payment.get("status") == "approved" and external_reference.startswith("REGISTRATION-"):
            registration_id = external_reference.removeprefix("REGISTRATION-")
            Registration.todas_as_igrejas.filter(pk=registration_id, church=church).update(
                payment_status=Registration.PaymentStatus.PAID,
                amount_paid=payment.get("transaction_amount", 0),
            )
        return HttpResponse(status=200)


class RegistrationDoneView(PublicChurchMixin, DetailView):
    """Página de confirmação — mostra o QR code de check-in (pra
    apresentar na entrada do evento) quando a inscrição está confirmada;
    quando caiu na lista de espera, mostra isso em vez do QR."""

    model = Registration
    template_name = "events/registration_done.html"
    context_object_name = "registration"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        registration = context["registration"]
        if not registration.on_waitlist:
            checkin_url = self.request.build_absolute_uri(
                reverse("events:checkin", args=[registration.checkin_token])
            )
            context["checkin_qr_data_uri"] = qr_data_uri(checkin_url)
        return context


class RegistrationCheckInView(IsChurchManagerMixin, View):
    """Endpoint que o QR code de check-in aponta — pensado pra ser aberto
    escaneando com a câmera comum do celular (não precisa de nenhum app/lib
    de leitura de QR): o QR só encoda essa URL, e quem escaneia é sempre um
    membro da equipe já logado no navegador do próprio aparelho."""

    template_name = "events/registration_checkin.html"

    def get(self, request, token):
        registration = get_object_or_404(Registration, checkin_token=token)
        already_checked_in = registration.checked_in_at is not None
        if not already_checked_in:
            registration.checked_in_at = timezone.now()
            registration.save(update_fields=["checked_in_at"])
        return render(request, self.template_name, {
            "registration": registration, "already_checked_in": already_checked_in,
        })


class RegistrationPromoteView(IsChurchManagerMixin, View):
    """Promove uma inscrição da lista de espera pra confirmada — manual,
    porque a secretaria costuma saber melhor do que uma regra automática
    quem priorizar quando abre uma vaga. Avisa a pessoa pela fila de
    WhatsApp (não envia na hora — mesmo motivo de sempre: evitar rajada)."""

    def post(self, request, slug, pk):
        registration = get_object_or_404(Registration, pk=pk, event__slug=slug, on_waitlist=True)
        registration.on_waitlist = False
        registration.save(update_fields=["on_waitlist"])

        if registration.phone:
            if registration.event.is_paid:
                payment_url = request.build_absolute_uri(
                    reverse(
                        "events_public:register_payment",
                        args=[registration.church.slug, slug, pk],
                    )
                )
                text = (
                    f"Boa notícia! Abriu uma vaga em \"{registration.event.title}\" e você foi "
                    f"chamado(a) da lista de espera. Finalize o pagamento aqui: {payment_url}"
                )
            else:
                text = f"Boa notícia! Sua vaga em \"{registration.event.title}\" foi confirmada."
            WhatsAppMessage.objects.create(
                church=registration.church,
                person=registration.person, phone=normalize_phone(registration.phone), message=text,
                campaign_label=f"Promoção de lista de espera — {registration.event.title}",
                created_by=request.user,
            )
        if registration.person and hasattr(registration.person, "user_account"):
            enviar_push_para_usuario(
                registration.person.user_account,
                title="Vaga liberada!",
                body=f"Sua vaga em \"{registration.event.title}\" foi confirmada.",
                url=reverse("events_public:detail", args=[registration.church.slug, registration.event.slug]),
            )
        messages.success(request, f"{registration.full_name} promovido(a) da lista de espera.")
        return redirect("events:registrations", slug=slug)


class RegistrationListView(IsChurchManagerMixin, ListView):
    template_name = "events/registration_list.html"
    context_object_name = "registrations"
    paginate_by = 50

    def get_queryset(self):
        self.event = get_object_or_404(Event, slug=self.kwargs["slug"])
        return self.event.registrations.order_by("-registered_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["event"] = self.event
        return context


class RegistrationMarkPaidView(IsChurchManagerMixin, View):
    """Confirmação manual de pagamento — sem gateway real integrado, é a
    secretaria quem verifica o recebimento do PIX e marca aqui."""

    def post(self, request, slug, pk):
        registration = get_object_or_404(Registration, pk=pk, event__slug=slug)
        registration.payment_status = Registration.PaymentStatus.PAID
        registration.amount_paid = registration.event.price
        registration.save(update_fields=["payment_status", "amount_paid"])
        messages.success(request, f"Pagamento de {registration.full_name} confirmado.")
        return redirect("events:registrations", slug=slug)


class RegistrationExportView(IsChurchManagerMixin, View):
    def get(self, request, slug):
        event = get_object_or_404(Event, slug=slug)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="inscritos-{event.slug}.csv"'
        # BOM escrito manualmente (uma única vez) para o Excel reconhecer
        # UTF-8 — usar charset="utf-8-sig" na response faria o Django
        # prefixar um BOM a CADA chamada de write() (uma por linha do CSV
        # via csv.writer), corrompendo todas as linhas menos a primeira.
        response.write("﻿")

        writer = csv.writer(response)
        writer.writerow([
            "Nome", "Telefone", "E-mail", "Status de pagamento", "Valor pago",
            "Lista de espera", "Check-in", "Inscrito em",
        ])
        for reg in event.registrations.order_by("full_name"):
            writer.writerow([
                reg.full_name, reg.phone, reg.email,
                reg.get_payment_status_display(), reg.amount_paid,
                "Sim" if reg.on_waitlist else "Não",
                reg.checked_in_at.strftime("%d/%m/%Y %H:%M") if reg.checked_in_at else "",
                reg.registered_at.strftime("%d/%m/%Y %H:%M"),
            ])
        return response
