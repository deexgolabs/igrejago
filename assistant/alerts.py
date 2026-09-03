"""Aviso automático pra secretaria — cadastro pendente novo (assistente
de IA ou link público de atualização) ou pedido de atendimento humano.
Mesmo padrão de `notifications.management.commands.verificar_conexao_whatsapp`:
e-mail pra `Church.admin_alert_emails` (lista separada por vírgula), sem
nada configurado não faz nada, erro de envio nunca propaga (só loga) —
um problema no SMTP não pode derrubar o fluxo principal (webhook do
WhatsApp, formulário público)."""

import logging

from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _destinatarios(church):
    return [email.strip() for email in church.admin_alert_emails.split(",") if email.strip()]


def avisar_novo_draft(draft):
    church = draft.church
    if not church.admin_alert_emails:
        return
    nome = draft.data.get("full_name") or (draft.person.full_name if draft.person_id else "alguém")
    acao = "atualizou os dados" if draft.person_id else "se cadastrou"
    try:
        send_mail(
            subject=f"Cadastro pendente de revisão — {nome}",
            message=(
                f"{nome} {acao} pelo assistente ({draft.get_origin_display()}) — entre no sistema, em "
                '"Cadastros pendentes", pra revisar e confirmar.'
            ),
            from_email=None, recipient_list=_destinatarios(church), fail_silently=False,
        )
    except Exception:
        logger.exception("Falha ao avisar cadastro pendente (igreja %s)", church.pk)


def avisar_atendimento_humano(conversation):
    church = conversation.church
    if not church.admin_alert_emails:
        return
    nome = conversation.person.full_name if conversation.person_id else conversation.phone
    try:
        send_mail(
            subject=f"Pedido de atendimento humano no WhatsApp — {nome}",
            message=(
                f'{nome} pediu pra falar com a secretaria pelo assistente de WhatsApp — entre no sistema, '
                'em "Atendimento humano (WhatsApp)", pra responder.'
            ),
            from_email=None, recipient_list=_destinatarios(church), fail_silently=False,
        )
    except Exception:
        logger.exception("Falha ao avisar pedido de atendimento humano (igreja %s)", church.pk)
