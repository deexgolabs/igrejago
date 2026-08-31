"""Envio de notificação push do navegador (Web Push/VAPID). Segue o mesmo
padrão de "prepared, not integrated" das outras integrações externas deste
projeto (Evolution API, Mercado Pago): sem `VAPID_PRIVATE_KEY` configurada
ou sem `pywebpush` instalado, simplesmente não envia nada — quem chama não
precisa checar isso antes, e nada quebra em dev/teste."""

import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def enviar_push_para_usuario(user, *, title, body, url="/"):
    """Manda a mesma notificação pra todas as inscrições push do usuário
    (pode ter mais de uma: celular, desktop...). Remove sozinha qualquer
    inscrição que o navegador já invalidou (410 Gone) — não tem outro jeito
    de saber que "descadastrou" além de tentar enviar e ver o erro."""
    if not (settings.VAPID_PRIVATE_KEY and settings.VAPID_PUBLIC_KEY):
        return 0

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush não instalado — notificação push não enviada.")
        return 0

    from notifications.models import PushSubscription

    payload = json.dumps({"title": title, "body": body, "url": url})
    sent = 0
    for sub in PushSubscription.objects.filter(user=user):
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_CLAIMS_EMAIL},
            )
            sent += 1
        except WebPushException as exc:
            status_code = getattr(exc.response, "status_code", None)
            if status_code == 410:
                sub.delete()
            else:
                logger.warning("Falha ao enviar push para %s: %s", user, exc)
    return sent
