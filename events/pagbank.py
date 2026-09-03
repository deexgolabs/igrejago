"""Chamada crua à API de Pedidos do PagBank pra checkout de evento pago
— mesmo padrão de `events/mercadopago.py`, duplicado em vez de
generalizado com `finance/pagbank.py` de propósito (mesmo raciocínio já
documentado em `finance/mercadopago.py`: dois fluxos pequenos e
independentes). Ver `finance/pagbank.py` pra nota de honestidade sobre
o formato da API (mesma fonte, mesma data de verificação)."""

import requests

API_BASE = "https://api.pagseguro.com"


def criar_pedido(*, token, registration, notification_url):
    """Cria um pedido PIX pra uma inscrição de evento pago. Devolve
    (order_id, qr_image_url, qr_copia_cola) — os dois últimos podem vir
    `None` se o formato da resposta não bater com o esperado."""
    event = registration.event
    payload = {
        "customer": {
            "name": registration.full_name,
            "email": registration.email or None,
            "tax_id": "00000000000",
        },
        "charges": [{
            "amount": {"value": int(event.price * 100), "currency": "BRL"},
            "payment_method": {"type": "PIX"},
        }],
        "notification_urls": [notification_url],
        "reference_id": f"REGISTRATION-{registration.pk}",
    }
    response = requests.post(
        f"{API_BASE}/orders", json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()

    order_id = data.get("id", "")
    qr_image_url = None
    qr_copia_cola = None
    for qr in data.get("qr_codes", []) or []:
        qr_copia_cola = qr_copia_cola or qr.get("text")
        for link in qr.get("links", []) or []:
            if link.get("rel") in ("QRCODE.PNG", "img", "image"):
                qr_image_url = link.get("href")
    return order_id, qr_image_url, qr_copia_cola


def consultar_pedido(*, token, order_id):
    response = requests.get(
        f"{API_BASE}/orders/{order_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def pedido_esta_pago(pedido):
    return any(charge.get("status") == "PAID" for charge in pedido.get("charges", []) or [])
