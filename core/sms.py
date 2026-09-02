"""Envio de SMS — "preparado, não integrado" (mesmo padrão de Sentry/
Web Push/Evolution API, ver `church_crm/settings.py` e `core/push.py`):
sem um provedor escolhido ainda, cai sempre no fallback de console — a
fila (`notifications.SMSMessage`/`processar_fila_sms`) continua
funcionando e testável sem nenhuma credencial real. Quando escolher um
provedor (Twilio, Zenvia, Total Voice, AWS SNS etc.), troque o corpo
desta função pela chamada real — mesmo formato/assinatura de
`core.whatsapp.enviar_whatsapp()`, que já resolve exatamente esse
problema pro WhatsApp."""

import logging
import sys

logger = logging.getLogger(__name__)


def enviar_sms(phone, message, *, church_config):
    """Devolve (True, "") em sucesso ou (False, "motivo") em falha.
    SEMPRE cai no fallback de console por enquanto: não existe provedor
    de SMS escolhido pra inventar o formato de uma chamada real (ver
    `settings.SMS_PROVIDER`/`SMS_API_KEY`/`SMS_API_SECRET`, hoje só
    documentando o contrato, sem uso ainda)."""
    if not phone:
        return False, "Sem telefone"

    _print_safe(f"[SMS — console fallback, sem provedor configurado] Para {phone}: {message}")
    return True, ""


def _print_safe(text):
    """Mesmo motivo de `core.whatsapp._print_safe` — `print()` sozinho
    quebra em console Windows/cp1252 se a mensagem tiver emoji/acento."""
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, errors="replace").decode(encoding), flush=True)
