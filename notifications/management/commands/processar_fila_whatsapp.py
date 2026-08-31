"""Processa a fila de WhatsApp de TODAS as igrejas ativas, uma de cada
vez: pega até `Church.whatsapp_batch_size` mensagens PENDING já vencidas
(agendadas pro passado ou sem agendamento) + mensagens FAILED que ainda
não bateram `whatsapp_max_retries`, e envia uma a uma, esperando
`Church.whatsapp_send_interval_seconds` entre cada envio. Pensado pra
rodar via cron a cada 1-5 minutos — cada execução processa um lote por
igreja e sai; o resto (se sobrar) fica pra próxima.

Cada igreja é processada dentro de `tenant_context(church)` — o mesmo
`WhatsAppMessage.objects` de sempre já filtra sozinho pra igreja atual,
sem precisar reescrever a query com `todas_as_igrejas` espalhado aqui.

Mandar um lote inteiro de uma vez, sem intervalo, é o jeito mais rápido de
um número real ser marcado como spam/banido pelo WhatsApp — esse intervalo
não é um detalhe cosmético, é o motivo desse comando existir separado de
só chamar `enviar_whatsapp()` em loop."""

import time

from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db.models import F, Q
from django.utils import timezone

from core.billing import whatsapp_liberado
from core.models import Church
from core.tenant_context import tenant_context
from core.whatsapp import enviar_whatsapp
from notifications.models import WhatsAppMessage


class Command(BaseCommand):
    help = "Envia as mensagens pendentes (e reenvia as que falharam) da fila de WhatsApp de cada igreja, com intervalo."

    def handle(self, *args, **options):
        total_sent = total_failed = total_emailed = total_processed = 0
        for church_config in Church.objects.exclude(status=Church.Status.SUSPENDED):
            if not church_config.email_confirmed:
                self.stdout.write(f"[{church_config.name}] e-mail não confirmado — pulando.")
                continue
            if not whatsapp_liberado(church_config):
                self.stdout.write(f"[{church_config.name}] WhatsApp não incluído no plano — pulando.")
                continue
            # Sem WhatsApp configurado, `enviar_whatsapp()` já cai sozinho
            # no fallback de console (ver core/whatsapp.py) — a fila
            # continua sendo processada (e testável) sem credencial real,
            # igual sempre foi antes da multi-tenência.
            with tenant_context(church_config):
                sent, failed, emailed, total = self._processar_igreja(church_config)
            total_sent += sent
            total_failed += failed
            total_emailed += emailed
            total_processed += total

        self.stdout.write(self.style.SUCCESS(
            f"{total_sent} enviada(s), {total_failed} falharam ({total_emailed} com fallback por e-mail). "
            f"{total_processed} processada(s) neste lote, em todas as igrejas."
        ))

    def _processar_igreja(self, church_config):
        eligible = (
            WhatsAppMessage.objects.filter(
                Q(status=WhatsAppMessage.Status.PENDING)
                & (Q(scheduled_for__isnull=True) | Q(scheduled_for__lte=timezone.now()))
                | Q(status=WhatsAppMessage.Status.FAILED, retry_count__lt=church_config.whatsapp_max_retries)
            )
            .order_by("created_at")[: church_config.whatsapp_batch_size]
        )

        sent, failed, emailed = 0, 0, 0
        total = len(eligible)
        for index, msg in enumerate(eligible):
            is_retry = msg.status == WhatsAppMessage.Status.FAILED
            ok, error, external_id = enviar_whatsapp(msg.phone, msg.message, church_config=church_config)
            if ok:
                msg.status = WhatsAppMessage.Status.SENT
                msg.sent_at = timezone.now()
                msg.external_id = external_id
                sent += 1
                new_retry_count = msg.retry_count
            else:
                msg.status = WhatsAppMessage.Status.FAILED
                msg.error_message = error
                new_retry_count = msg.retry_count + 1 if is_retry else msg.retry_count
                if is_retry:
                    msg.retry_count = F("retry_count") + 1
                failed += 1
            msg.save(update_fields=["status", "sent_at", "error_message", "external_id", "retry_count"])

            # Depois de esgotar as tentativas automáticas, a mensagem fica
            # FAILED parada pra sempre (só reenvio manual a resgata) — se a
            # pessoa tem e-mail cadastrado, manda por lá como último
            # recurso, pra não depender só de alguém notar na fila.
            if not ok and is_retry and new_retry_count >= church_config.whatsapp_max_retries:
                if self._send_email_fallback(msg, church_config):
                    emailed += 1

            is_last = index == total - 1
            if not is_last:
                time.sleep(church_config.whatsapp_send_interval_seconds)

        return sent, failed, emailed, total

    @staticmethod
    def _send_email_fallback(msg, church_config):
        if not (msg.person and msg.person.email):
            return False
        try:
            send_mail(
                subject=f"Mensagem de {church_config.name or 'sua igreja'}",
                message=msg.message,
                from_email=None,
                recipient_list=[msg.person.email],
                fail_silently=False,
            )
            return True
        except Exception:
            return False
