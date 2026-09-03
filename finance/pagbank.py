"""Chamada crua à API de Pedidos do PagBank (developer.pagbank.com.br)
— segundo gateway de pagamento, mesmo padrão sem SDK de
`finance/mercadopago.py`. `token` é da PRÓPRIA igreja (gerado no painel
de desenvolvedor do PagBank), não infraestrutura desta plataforma.

Nota de honestidade: implementado a partir da documentação pública
atual do PagBank (confirmada via consulta em 02/09/2026 — `POST
/orders` cria, `GET /orders/{id}` consulta, ambos com `Authorization:
Bearer <token>`), nunca chamado com uma credencial de produção real. O
formato exato da resposta do QR Code PIX (`qr_codes[].links[]`) segue o
padrão documentado, mas `criar_pedido` extrai o link de forma
defensiva — se o formato real vier diferente, devolve o texto "copia e
cola" como alternativa em vez de quebrar."""

import requests

API_BASE = "https://api.pagseguro.com"


def criar_pedido(*, token, donation, notification_url):
    """Cria um pedido PIX pra uma doação avulsa. Devolve (order_id,
    qr_image_url, qr_copia_cola) — os dois últimos podem vir `None` se o
    formato da resposta não bater com o esperado (nunca levanta exceção
    por isso, só por falha HTTP de verdade)."""
    payload = {
        "customer": {
            "name": donation.person.full_name if donation.person else "Doador",
            "email": donation.person.email if donation.person and donation.person.email else None,
            "tax_id": "00000000000",
        },
        "charges": [{
            "amount": {"value": int(donation.amount * 100), "currency": "BRL"},
            "payment_method": {"type": "PIX"},
        }],
        "notification_urls": [notification_url],
        "reference_id": f"DOACAO-{donation.pk}",
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
    """Reconsulta o pedido direto na API — nunca confiar no corpo do
    webhook pra decidir se está mesmo pago, mesmo princípio já usado nas
    outras integrações de gateway deste projeto."""
    response = requests.get(
        f"{API_BASE}/orders/{order_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def pedido_esta_pago(pedido):
    """`True` se ALGUMA cobrança do pedido já foi paga — status
    documentado pelo PagBank como `PAID` por cobrança individual
    (`charges[].status`), não no pedido como um todo."""
    return any(charge.get("status") == "PAID" for charge in pedido.get("charges", []) or [])
