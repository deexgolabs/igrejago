"""Geração de QR code em base64 (data URI) — compartilhada entre o QR do
PIX de pagamento, o QR de check-in na entrada do evento e o QR de
configuração do 2FA (TOTP)."""

import base64
import io

import qrcode


def qr_data_uri(payload):
    img = qrcode.make(payload)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
