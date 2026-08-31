"""Chamadas cruas à API REST do Mercado Pago (sem o SDK oficial — mesmo
padrão do crm-odonto) para checkout de eventos pagos. O token vem de
`ChurchConfig.mercadopago_access_token`, não de variável de ambiente, já que
este é um sistema single-tenant e cada igreja tem sua própria conta."""

import requests

API_BASE = "https://api.mercadopago.com"


def criar_preferencia(*, access_token, registration, back_url_success, back_url_pending, notification_url):
    """Cria uma preferência de checkout para uma inscrição de evento pago.
    Devolve a URL de checkout (`init_point`) para redirecionar a pessoa."""
    event = registration.event
    payload = {
        "items": [{
            "title": event.title,
            "quantity": 1,
            "unit_price": float(event.price),
            "currency_id": "BRL",
        }],
        "payer": {"name": registration.full_name, "email": registration.email or None},
        "external_reference": f"REGISTRATION-{registration.pk}",
        "back_urls": {
            "success": back_url_success,
            "pending": back_url_pending,
            "failure": back_url_pending,
        },
        "notification_url": notification_url,
        "auto_return": "approved",
    }
    response = requests.post(
        f"{API_BASE}/checkout/preferences",
        json=payload,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["init_point"]


def consultar_pagamento(*, access_token, payment_id):
    """Reconsulta o pagamento direto na API — nunca confiar no corpo do
    webhook para decidir se algo foi realmente pago."""
    response = requests.get(
        f"{API_BASE}/v1/payments/{payment_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
