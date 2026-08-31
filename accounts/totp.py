"""TOTP (RFC 6238) puro, sem dependência externa (`pyotp`/`django-otp`) —
mesma filosofia de `events/pix.py` implementar o CRC16 do PIX na mão em vez
de puxar uma lib só pra isso. Compatível com qualquer app autenticador
padrão (Google Authenticator, Authy, etc.): SHA1, 6 dígitos, passo de 30s."""

import base64
import hashlib
import hmac
import os
import struct
import time

_DIGITS = 6
_STEP_SECONDS = 30
_WINDOW = 1  # aceita o código do passo atual + 1 pra trás/frente (relógio do celular meio dessincronizado)


def generate_secret():
    """Segredo aleatório de 160 bits, em base32 (formato que os apps
    autenticadores esperam pra digitar/escanear)."""
    return base64.b32encode(os.urandom(20)).decode("ascii")


def _hotp(secret, counter):
    # base32 exige o comprimento múltiplo de 8 — completa com "=" se faltar.
    padded = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** _DIGITS)
    return str(code).zfill(_DIGITS)


def totp_now(secret, for_time=None):
    """Código válido pro instante atual (ou `for_time`, em epoch segundos —
    só usado nos testes, pra não depender de `time.time()` real)."""
    counter = int((for_time if for_time is not None else time.time()) // _STEP_SECONDS)
    return _hotp(secret, counter)


def verify_totp(secret, code):
    """Aceita o código do passo atual e de ±1 passo (±30s) de tolerância,
    já que o relógio do celular de quem está digitando raramente bate
    exatamente com o do servidor."""
    if not code or not code.strip().isdigit():
        return False
    code = code.strip()
    counter_now = int(time.time() // _STEP_SECONDS)
    return any(_hotp(secret, counter_now + offset) == code for offset in range(-_WINDOW, _WINDOW + 1))


def otpauth_uri(*, secret, username, issuer="Church CRM"):
    """URI `otpauth://` que o QR code de configuração encoda — todo app
    autenticador entende esse formato pra preencher segredo/emissor/conta
    sozinho ao escanear."""
    from urllib.parse import quote

    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={_DIGITS}&period={_STEP_SECONDS}"
