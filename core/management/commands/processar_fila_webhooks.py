"""Processa a fila de entregas de webhook (`core.WebhookDelivery`) de
TODAS as igrejas — mesmo formato dos outros processadores de fila deste
projeto (WhatsApp/e-mail/SMS): pega as `PENDING` e faz o POST de
verdade agora, fora da request original que gerou o evento.

Assina o corpo com HMAC-SHA256 usando `WebhookSubscription.secret`,
mandado no cabeçalho `X-IgrejaGo-Signature` — mesma convenção usada por
GitHub/Stripe, pra quem recebe (ex.: um Zap no Zapier com um passo de
verificação) conseguir confirmar que o payload veio realmente daqui."""

import hashlib
import hmac
import json

import requests
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.models import Church, WebhookDelivery
from core.tenant_context import tenant_context

MAX_ATTEMPTS = 5


class Command(BaseCommand):
    help = "Entrega as chamadas de webhook pendentes de cada igreja."

    def handle(self, *args, **options):
        total_sent = total_failed = 0
        for church_config in Church.objects.exclude(status=Church.Status.SUSPENDED):
            with tenant_context(church_config):
                sent, failed = self._processar_igreja()
            total_sent += sent
            total_failed += failed

        self.stdout.write(self.style.SUCCESS(f"{total_sent} entregue(s), {total_failed} falharam."))

    def _processar_igreja(self):
        eligible = WebhookDelivery.objects.filter(
            Q(status=WebhookDelivery.Status.PENDING)
            | Q(status=WebhookDelivery.Status.FAILED, attempt_count__lt=MAX_ATTEMPTS),
        ).select_related("subscription").order_by("created_at")[:100]

        sent = failed = 0
        for delivery in eligible:
            body = json.dumps(delivery.payload).encode()
            signature = hmac.new(delivery.subscription.secret.encode(), body, hashlib.sha256).hexdigest()
            try:
                response = requests.post(
                    delivery.subscription.url,
                    data=body,
                    headers={"Content-Type": "application/json", "X-IgrejaGo-Signature": signature},
                    timeout=10,
                )
                delivery.response_status_code = response.status_code
                if response.ok:
                    delivery.status = WebhookDelivery.Status.SENT
                    delivery.sent_at = timezone.now()
                    sent += 1
                else:
                    delivery.status = WebhookDelivery.Status.FAILED
                    failed += 1
            except Exception:
                delivery.status = WebhookDelivery.Status.FAILED
                failed += 1
            delivery.attempt_count += 1
            delivery.save(update_fields=["status", "response_status_code", "attempt_count", "sent_at"])

        return sent, failed
