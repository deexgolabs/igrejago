"""Gera o payload PIX "Copia e Cola" (BR Code, padrão EMV do Banco Central)
localmente — sem depender de conta em gateway de pagamento. O evento pago só
precisa que a igreja preencha a própria chave PIX em ChurchConfig; a
confirmação do pagamento continua manual (ver events.views.RegistrationMarkPaidView),
já que sem uma conta de gateway real não existe webhook para confirmar
automaticamente."""


def _tlv(id_, value):
    """Campo Tag-Length-Value do formato EMV usado pelo BR Code."""
    length = f"{len(value):02d}"
    return f"{id_}{length}{value}"


def _crc16_ccitt(payload: str) -> str:
    """CRC16-CCITT (polinômio 0x1021, inicial 0xFFFF) — o checksum exigido
    no final de todo payload PIX, calculado à mão (sem lib externa)."""
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def build_pix_payload(*, key: str, receiver_name: str, receiver_city: str, amount, txid: str) -> str:
    """Monta o payload completo. `txid` deve ser alfanumérico, até 25
    caracteres (usamos o id da Registration)."""
    merchant_account = (
        _tlv("00", "br.gov.bcb.pix") + _tlv("01", key)
    )
    additional_data = _tlv("05", txid[:25] or "***")

    payload_without_crc = (
        _tlv("00", "01")  # Payload Format Indicator
        + _tlv("26", merchant_account)  # Merchant Account Information (PIX)
        + _tlv("52", "0000")  # Merchant Category Code
        + _tlv("53", "986")  # Currency: BRL
        + _tlv("54", f"{float(amount):.2f}")  # Transaction amount
        + _tlv("58", "BR")  # Country code
        + _tlv("59", receiver_name[:25] or "IGREJA")  # Merchant name
        + _tlv("60", receiver_city[:15] or "CIDADE")  # Merchant city
        + _tlv("62", additional_data)  # Additional data field
        + "6304"  # CRC tag + length, valor calculado a seguir
    )
    return payload_without_crc + _crc16_ccitt(payload_without_crc)
