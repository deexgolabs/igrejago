"""Envio de e-mail EM MASSA (campanha) — diferente das transacionais já
existentes (`send_mail()` direto em `core/views.py`, texto puro): aqui
o texto digitado pela secretaria é simples, mas embrulhado num shell
HTML mínimo com a cor/nome da igreja no envio, pra não chegar cru
(sem nenhuma cara) na caixa de entrada de quem recebe. Usa o mesmo
`EMAIL_BACKEND` já configurado em `settings.py` (console em dev, SMTP
em produção) — nenhuma credencial nova."""

from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape, linebreaks


def enviar_email_campanha(to_email, subject, body, *, church_config):
    """Devolve (True, "") em sucesso ou (False, "motivo") em falha —
    mesma assinatura de `core.whatsapp.enviar_whatsapp` (menos o
    external_id, que e-mail não tem aqui)."""
    if not to_email:
        return False, "Sem e-mail"

    try:
        email = EmailMultiAlternatives(subject=subject, body=body, to=[to_email])
        email.attach_alternative(_wrap_html(body, church_config), "text/html")
        email.send(fail_silently=False)
        return True, ""
    except Exception as exc:
        return False, str(exc)[:255]


def _wrap_html(body, church_config):
    cor = getattr(church_config, "brand_color", None) or "#2563eb"
    nome = getattr(church_config, "name", None) or "Igreja"
    return (
        '<div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">'
        f'<div style="background:{cor}; color:#fff; padding:16px; border-radius:8px 8px 0 0; font-weight:bold;">{escape(nome)}</div>'
        f'<div style="border:1px solid #e2e8f0; border-top:none; padding:20px; border-radius:0 0 8px 8px; color:#1e293b;">{linebreaks(escape(body))}</div>'
        "</div>"
    )
