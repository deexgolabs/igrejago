"""Disparo de webhook de saída — o evento em si (`disparar_webhook`)
só CRIA um `WebhookDelivery(status=PENDING)` por assinatura ativa
daquele tipo; o POST de verdade pra URL de terceiro acontece depois,
via `processar_fila_webhooks` (cron), pelo mesmo motivo de sempre neste
projeto (WhatsApp, e-mail, SMS): nunca travar a request do usuário
esperando a resposta de um servidor de terceiro que pode estar lento
ou fora do ar."""

from core.models import WebhookDelivery, WebhookSubscription


def disparar_webhook(church, event_type, payload):
    """`payload` já deve ser um dict serializável em JSON (sem instâncias
    de model — o chamador monta o dict com só os campos que fazem
    sentido expor). Silenciosamente não faz nada se não houver nenhuma
    assinatura ativa daquele tipo — a maioria das igrejas nunca vai
    configurar um webhook, e isso não pode custar uma query extra
    perceptível em todo `PersonCreateView`/`TransactionCreateView`."""
    subscriptions = WebhookSubscription.objects.filter(
        church=church, event_type=event_type, is_active=True,
    )
    if not subscriptions.exists():
        return

    WebhookDelivery.objects.bulk_create([
        WebhookDelivery(church=church, subscription=sub, event_type=event_type, payload=payload)
        for sub in subscriptions
    ])
