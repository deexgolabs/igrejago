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


def criar_assinatura_dizimo(*, access_token, pledge, payer_email, back_url, notification_url):
    """Cria uma assinatura recorrente mensal (dízimo automático) —
    mesmíssimo formato de `core.mercadopago_billing.criar_assinatura()`
    (API de Assinaturas/Preapproval), só que com o token DA IGREJA (não
    da plataforma) e o valor que o próprio membro escolheu. Devolve
    (preapproval_id, init_point) — redirecione a pessoa pro `init_point`
    pra autorizar com o cartão."""
    payload = {
        "reason": f"Dízimo mensal — {pledge.person.full_name}",
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": float(pledge.monthly_amount),
            "currency_id": "BRL",
        },
        "back_url": back_url,
        "notification_url": notification_url,
        "payer_email": payer_email,
        "external_reference": f"DIZIMO-{pledge.pk}",
        "status": "pending",
    }
    response = requests.post(
        f"{API_BASE}/preapproval",
        json=payload,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    return data["id"], data["init_point"]


def consultar_assinatura(*, access_token, preapproval_id):
    """Reconsulta a assinatura direto na API — nunca confiar no corpo do
    webhook pra decidir se está mesmo autorizada/ativa."""
    response = requests.get(
        f"{API_BASE}/preapproval/{preapproval_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def consultar_pagamento_autorizado(*, access_token, payment_id):
    """Reconsulta uma cobrança mensal de uma assinatura — endpoint
    DIFERENTE de `consultar_pagamento` (esse é `/authorized_payments/`,
    específico da API de Assinaturas; `/v1/payments/` é pra pagamento
    avulso). Formato confirmado na documentação oficial do Mercado Pago
    (webhook `subscription_authorized_payment`)."""
    response = requests.get(
        f"{API_BASE}/authorized_payments/{payment_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def cancelar_assinatura(*, access_token, preapproval_id):
    """Cancela a assinatura (o membro pode voltar atrás depois se quiser
    assinar de novo, mas não reativa a mesma — cria uma nova)."""
    response = requests.put(
        f"{API_BASE}/preapproval/{preapproval_id}",
        json={"status": "cancelled"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
