"""Chamadas cruas à API de Assinaturas (Preapproval) do Mercado Pago —
mesmo estilo sem SDK de `events/mercadopago.py`/`finance/mercadopago.py`
(sempre reconsulta antes de confiar). Usa a conta da PLATAFORMA
(`settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN`), diferente da conta de
cada igreja usada nos outros dois módulos — aqui é a PLATAFORMA cobrando
a igreja, não a igreja recebendo de um membro/inscrito."""

import requests

API_BASE = "https://api.mercadopago.com"


def criar_assinatura(*, access_token, plano_key, plano_info, church, payer_email, back_url, notification_url):
    """Cria uma assinatura recorrente mensal. Devolve (preapproval_id,
    init_point) — redirecione a igreja pro `init_point` pra confirmar."""
    payload = {
        "reason": f"Church CRM — Plano {plano_info['nome']}",
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": float(plano_info["preco"]),
            "currency_id": "BRL",
        },
        "back_url": back_url,
        "notification_url": notification_url,
        "payer_email": payer_email,
        "external_reference": f"CHURCH-{church.pk}-{plano_key}",
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
    webhook pra decidir se está mesmo autorizada."""
    response = requests.get(
        f"{API_BASE}/preapproval/{preapproval_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
