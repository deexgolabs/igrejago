"""Envio de e-mail EM MASSA (campanha) — diferente das transacionais já
existentes (`send_mail()` direto em `core/views.py`, texto puro): aqui
o texto digitado pela secretaria é simples, mas embrulhado num shell
HTML mínimo com a cor/nome da igreja, um pixel de rastreio de abertura,
os links reescritos pra rastrear clique, e um rodapé de descadastro —
"e-mail marketing de verdade", não só um disparo. Usa o mesmo
`EMAIL_BACKEND` já configurado em `settings.py` (console em dev, SMTP
em produção) — nenhuma credencial nova.

Reputação de domínio (SPF/DKIM/DMARC) não é algo que dá pra configurar
daqui — depende do provedor SMTP/DNS da igreja. O que este módulo faz é
o lado que É automatizável: cabeçalho `List-Unsubscribe` (RFC 8058,
sinal real que Gmail/Outlook levam em conta pra reputação, não só
cosmético) e honrar `Person.email_opted_out_at` (ver
`people.views.EmailCampaignSendView`)."""

import re
from urllib.parse import quote

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape, linebreaks

URL_PATTERN = re.compile(r'https?://[^\s<>"]+')


def enviar_email_campanha(to_email, subject, body, *, church_config, tracking_token):
    """Devolve (True, "") em sucesso ou (False, "motivo") em falha —
    mesma assinatura de `core.whatsapp.enviar_whatsapp` (menos o
    external_id). `tracking_token` é o `EmailMessage.tracking_token`
    dessa mensagem específica — usado nas 3 URLs públicas de rastreio."""
    if not to_email:
        return False, "Sem e-mail"

    base_url = settings.SITE_URL.rstrip("/")
    unsubscribe_url = f"{base_url}/mensagens/email/cancelar/{tracking_token}/"
    plain_body = f"{body}\n\n---\nNão quer mais receber? Cancelar inscrição: {unsubscribe_url}"

    try:
        email = EmailMultiAlternatives(
            subject=subject, body=plain_body, to=[to_email],
            headers={
                "List-Unsubscribe": f"<{unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        )
        email.attach_alternative(_wrap_html(body, church_config, tracking_token, base_url), "text/html")
        email.send(fail_silently=False)
        return True, ""
    except Exception as exc:
        return False, str(exc)[:255]


def _linkify_com_rastreio(body, tracking_token, base_url):
    """Troca cada URL http(s) do texto puro por um link que passa pelo
    redirect de clique antes de ir pro destino de verdade — feito ANTES
    de qualquer escape (senão `&` de query string vira `&amp;` e o
    redirect quebra); o texto ao redor é escapado normalmente, só a URL
    detectada vira um `<a>` de verdade."""
    parts = URL_PATTERN.split(body)
    urls = URL_PATTERN.findall(body)
    pieces = []
    for index, part in enumerate(parts):
        pieces.append(escape(part))
        if index < len(urls):
            original_url = urls[index]
            click_url = f"{base_url}/mensagens/email/clique/{tracking_token}/?url={quote(original_url, safe='')}"
            pieces.append(f'<a href="{click_url}" style="color:inherit;">{escape(original_url)}</a>')
    return "".join(pieces)


def _wrap_html(body, church_config, tracking_token, base_url):
    cor = getattr(church_config, "brand_color", None) or "#2563eb"
    nome = getattr(church_config, "name", None) or "Igreja"
    linked_body = _linkify_com_rastreio(body, tracking_token, base_url)
    unsubscribe_url = f"{base_url}/mensagens/email/cancelar/{tracking_token}/"
    pixel_url = f"{base_url}/mensagens/email/rastrear/{tracking_token}.gif"
    return (
        '<div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">'
        f'<div style="background:{cor}; color:#fff; padding:16px; border-radius:8px 8px 0 0; font-weight:bold;">{escape(nome)}</div>'
        f'<div style="border:1px solid #e2e8f0; border-top:none; padding:20px; border-radius:0 0 8px 8px; color:#1e293b;">'
        f'{linebreaks(linked_body)}'
        f'<p style="font-size:11px; color:#94a3b8; margin-top:16px; text-align:center;">'
        f'<a href="{unsubscribe_url}" style="color:#94a3b8;">Cancelar inscrição</a></p>'
        "</div>"
        f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="">'
        "</div>"
    )
