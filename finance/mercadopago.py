"""Chamada crua à API REST do Mercado Pago para o checkout de doação
avulsa do Portal do Membro — mesmo padrão de `events/mercadopago.py`
(sem SDK oficial), só que pra um valor livre em vez do preço fixo de um
evento. Duplicado em vez de generalizado com `events/mercadopago.py` de
propósito: são dois fluxos pequenos e independentes (evento x doação),
generalizar agora só criaria acoplamento sem necessidade real ainda."""

import requests

API_BASE = "https://api.mercadopago.com"


def criar_preferencia_doacao(*, access_token, donation, back_url_success, notification_url):
    """Cria uma preferência de checkout para uma doação avulsa. Devolve a
    URL de checkout (`init_point`) para redirecionar a pessoa."""
    payload = {
        "items": [{
            "title": "Doação",
            "quantity": 1,
            "unit_price": float(donation.amount),
            "currency_id": "BRL",
        }],
        "payer": {
            "name": donation.person.full_name if donation.person else None,
            "email": donation.person.email if donation.person and donation.person.email else None,
        },
        "external_reference": f"DOACAO-{donation.pk}",
        "back_urls": {
            "success": back_url_success,
            "pending": back_url_success,
            "failure": back_url_success,
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
    response = requests.get(
        f"{API_BASE}/v1/payments/{payment_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
