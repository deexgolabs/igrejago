"""Processa a fila de SMS de TODAS as igrejas ativas — mesmo formato de
`processar_fila_whatsapp.py`. Como `core.sms.enviar_sms` ainda cai
sempre no fallback de console (nenhum provedor escolhido — ver
docstring de `core/sms.py`), toda mensagem "envia" com sucesso por
enquanto; a lógica de retry já fica pronta pro dia em que um provedor
de verdade entrar e passar a falhar de vez em quando."""

from django.core.management.base import BaseCommand
from django.db.models import F, Q
from django.utils import timezone

from core.models import Church
from core.sms import enviar_sms
from core.tenant_context import tenant_context
from notifications.models import SMSMessage

SMS_MAX_RETRIES = 3


class Command(BaseCommand):
    help = "Envia os SMS pendentes (e reenvia os que falharam) da fila de cada igreja."

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
        eligible = list(SMSMessage.objects.filter(
            Q(status=SMSMessage.Status.PENDING)
            & (Q(scheduled_for__isnull=True) | Q(scheduled_for__lte=timezone.now()))
            | Q(status=SMSMessage.Status.FAILED, retry_count__lt=SMS_MAX_RETRIES),
        ).order_by("created_at")[:50])

        sent = failed = 0
        for msg in eligible:
            is_retry = msg.status == SMSMessage.Status.FAILED
            ok, error = enviar_sms(msg.phone, msg.message, church_config=church_config)
            if ok:
                msg.status = SMSMessage.Status.SENT
                msg.sent_at = timezone.now()
                sent += 1
            else:
                msg.status = SMSMessage.Status.FAILED
                msg.error_message = error
                if is_retry:
                    msg.retry_count = F("retry_count") + 1
                failed += 1
            msg.save(update_fields=["status", "sent_at", "error_message", "retry_count"])

        return sent, failed, len(eligible)
