"""Processa a fila de e-mail em massa de TODAS as igrejas ativas —
mesmo formato de `processar_fila_whatsapp.py`, só trocando o canal:
pega até `Church.email_batch_size` mensagens PENDING já vencidas (ou
sem agendamento) e manda uma a uma via `core.email_campaign.
enviar_email_campanha`. Sem intervalo forçado entre envios (SMTP não
tem o mesmo risco de banimento por rajada que o WhatsApp tem) — o
`email_batch_size` já limita quanto sai por execução, evitando estourar
cota diária do provedor."""

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.email_campaign import enviar_email_campanha
from core.models import Church
from core.tenant_context import tenant_context
from notifications.models import EmailMessage


class Command(BaseCommand):
    help = "Envia os e-mails de campanha pendentes da fila de cada igreja, em lotes."

    def handle(self, *args, **options):
        total_sent = total_failed = total_processed = 0
        for church_config in Church.objects.exclude(status=Church.Status.SUSPENDED):
            with tenant_context(church_config):
                sent, failed, total = self._processar_igreja(church_config)
            total_sent += sent
            total_failed += failed
            total_processed += total

        self.stdout.write(self.style.SUCCESS(
            f"{total_sent} enviado(s), {total_failed} falharam. "
            f"{total_processed} processado(s) neste lote, em todas as igrejas."
        ))

    def _processar_igreja(self, church_config):
        eligible = EmailMessage.objects.filter(
            status=EmailMessage.Status.PENDING,
        ).filter(
            Q(scheduled_for__isnull=True) | Q(scheduled_for__lte=timezone.now()),
        ).order_by("created_at")[: church_config.email_batch_size]

        sent = failed = 0
        for msg in eligible:
            # Confere de novo no momento do envio, não só na criação da
            # campanha (`people.EmailCampaignSendView`) — a pessoa pode
            # ter se descadastrado DEPOIS que a mensagem já tinha
            # entrado na fila, mas ANTES de sair de verdade.
            if msg.person_id and msg.person.email_opted_out_at:
                msg.status = EmailMessage.Status.CANCELLED
                msg.error_message = "Pessoa descadastrada antes do envio."
                msg.save(update_fields=["status", "error_message"])
                continue

            ok, error = enviar_email_campanha(
                msg.email, msg.subject, msg.body, church_config=church_config, tracking_token=msg.tracking_token,
            )
            if ok:
                msg.status = EmailMessage.Status.SENT
                msg.sent_at = timezone.now()
                sent += 1
            else:
                msg.status = EmailMessage.Status.FAILED
                msg.error_message = error
                failed += 1
            msg.save(update_fields=["status", "sent_at", "error_message"])

        return sent, failed, len(eligible)
