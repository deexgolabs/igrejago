"""Motor de atendimento do assistente de WhatsApp — ponto de entrada
único (`processar_mensagem_recebida`), chamado pelos dois webhooks
(`notifications.views.WhatsAppWebhookView`/`MetaWhatsAppWebhookView`)
via import tardio (evita import circular — este módulo nunca importa
`notifications.views`, só o contrário).

Fluxo: menu numerado (1. atualizar cadastro / 2. falar com secretaria /
3. pergunta livre) — a IA só é chamada na coleta de cadastro (extração
estruturada) ou na opção 3 (resposta livre), nunca no menu em si.
Cadastro/atualização NUNCA grava direto: vira `PersonDraft` pendente
de aprovação humana, só depois de a própria pessoa confirmar o eco no
chat (ver `_tratar_confirmacao`)."""

import logging
from datetime import timedelta

from django.utils import timezone

from assistant import ai, alerts, ratelimit
from assistant.models import Conversation, ConversationMessage, PersonDraft
from core import whatsapp

logger = logging.getLogger(__name__)

EXPIRACAO_HORAS = 6

_ROTULOS_CAMPO = {
    "full_name": "Nome", "phone": "Telefone", "email": "E-mail",
    "birth_date": "Nascimento", "gender": "Sexo", "marital_status": "Estado civil",
    "address": "Endereço", "city": "Cidade", "state": "UF", "zip_code": "CEP",
}

_MSG_IA_DESLIGADA = "No momento não temos atendimento automático por aqui — fale direto com a secretaria."
_MSG_LIMITE = 'Muitas mensagens em pouco tempo — tente de novo daqui a pouco ou digite "2" pra falar com a secretaria.'
_MSG_FALHA_IA = 'Não consegui responder agora — digite "2" pra falar com a secretaria.'
_MSG_MIDIA_NAO_SUPORTADA = "Só entendo mensagens de texto por enquanto — pode escrever o que precisa?"


def processar_mensagem_recebida(*, church, instance, phone, texto, raw):
    """Chamada pelos webhooks dentro de um `tenant_context(church)` já
    ativo — `Person`/`Conversation`.objects já filtram sozinhos por
    igreja. `instance` é `None` no canal Meta Cloud.

    `texto=None` (diferente de `""`) é o sinal que os webhooks mandam
    pra "chegou mídia (foto/áudio/vídeo/documento), não texto" — nesse
    caso responde um aviso fixo em vez de tentar processar como
    cadastro/pergunta (nunca chama a IA com isso). `texto=""`/ausente
    continua ignorado em silêncio (evento sem conteúdo útil nenhum)."""
    phone = _normalizar_telefone(phone)
    if not phone:
        return
    if not ratelimit.mensagem_permitida(church, phone):
        # Estourou o volume geral (não é o limite de IA) — nem
        # responde, pra não amplificar uma rajada com mais envio de
        # saída; só para de processar em silêncio.
        return
    if texto is None:
        _processar_midia_sem_texto(church, instance, phone, raw)
        return
    texto = texto.strip()
    if not texto:
        return

    conversation, created = Conversation.objects.get_or_create(
        church=church, phone=phone,
        defaults={"instance": instance, "state": Conversation.State.MENU},
    )
    if not created:
        if instance is not None:
            conversation.instance = instance
        if _esta_expirada(conversation):
            conversation.state = Conversation.State.MENU
            conversation.state_data = {}

    ConversationMessage.objects.create(
        church=church, conversation=conversation, direction=ConversationMessage.Direction.IN,
        body=texto, raw_payload=raw or {},
    )

    if conversation.person_id is None:
        pessoa = _buscar_person_por_telefone(phone)
        if pessoa is not None:
            conversation.person = pessoa

    if not church.ia_chat_enabled:
        resposta = _MSG_IA_DESLIGADA
    else:
        resposta = _despachar(church, conversation, texto)

    # Save único no fim — garante que `last_message_at` (auto_now)
    # sempre bate com "agora" e persiste qualquer mudança de state/
    # state_data/person/instance feita pelos handlers acima, sem
    # precisar de `update_fields` espalhado por cada um deles.
    conversation.save()

    if resposta:
        _responder(church, instance, conversation, resposta)


def _processar_midia_sem_texto(church, instance, phone, raw):
    """Mídia recebida (foto/áudio/vídeo/documento) — não passa pelo
    `_despachar` (não muda `state`, nunca chama IA), só registra no
    transcript e responde um aviso fixo. Mesmo gate de
    `ia_chat_enabled` do fluxo normal, pra não notificar quem nem tem
    o assistente ligado."""
    conversation, created = Conversation.objects.get_or_create(
        church=church, phone=phone,
        defaults={"instance": instance, "state": Conversation.State.MENU},
    )
    if not created and instance is not None:
        conversation.instance = instance
    ConversationMessage.objects.create(
        church=church, conversation=conversation, direction=ConversationMessage.Direction.IN,
        body="[mídia recebida]", raw_payload=raw or {},
    )
    conversation.save()
    resposta = _MSG_IA_DESLIGADA if not church.ia_chat_enabled else _MSG_MIDIA_NAO_SUPORTADA
    _responder(church, instance, conversation, resposta)


def _despachar(church, conversation, texto):
    # Escape global — digitar "menu"/"0" sempre volta pro início, em
    # QUALQUER estado (achado testando de verdade: sem isso, alguém
    # preso em COLETANDO_CADASTRO/AGUARDANDO_CONFIRMACAO não tinha
    # como sair só digitando "menu"/"2", já que cada handler só tratava
    # o próprio vocabulário — "2" em COLETANDO_CADASTRO, por exemplo,
    # ia direto pra extração de IA em vez de mudar de estado).
    normalizado = texto.strip().lower().rstrip(".")
    if normalizado in ("menu", "0") and conversation.state != Conversation.State.MENU:
        conversation.state = Conversation.State.MENU
        conversation.state_data = {}
        return _texto_menu(church)

    handlers = {
        Conversation.State.MENU: _tratar_menu,
        Conversation.State.COLETANDO_CADASTRO: _tratar_coleta,
        Conversation.State.AGUARDANDO_CONFIRMACAO: _tratar_confirmacao,
        Conversation.State.IA_LIVRE: _tratar_ia_livre,
        Conversation.State.AGUARDANDO_HUMANO: _tratar_aguardando_humano,
    }
    handler = handlers.get(conversation.state, _tratar_menu)
    return handler(church, conversation, texto)


def _texto_menu(church):
    return (
        f"Oi! Aqui é o assistente da {church.name}. Como posso ajudar?\n\n"
        "1 — Atualizar meu cadastro\n"
        "2 — Falar com a secretaria\n"
        "3 — Fazer uma pergunta"
    )


def _tratar_menu(church, conversation, texto):
    escolha = texto.strip().lower().rstrip(".")
    if escolha == "1":
        conversation.state = Conversation.State.COLETANDO_CADASTRO
        conversation.state_data = {}
        return (
            "Me conta seus dados numa mensagem só — pelo menos o nome completo; se quiser, "
            "também data de nascimento, e-mail e endereço."
        )
    if escolha == "2":
        conversation.state = Conversation.State.AGUARDANDO_HUMANO
        alerts.avisar_atendimento_humano(conversation)
        return "Vou avisar a secretaria — já já alguém te chama por aqui. (digite \"menu\" a qualquer momento pra voltar)"
    if escolha == "3":
        conversation.state = Conversation.State.IA_LIVRE
        return 'Pode perguntar! (digite "menu" a qualquer momento pra voltar)'
    return _texto_menu(church)


def _tratar_coleta(church, conversation, texto):
    if not ratelimit.ia_call_permitida(church, conversation.phone):
        return _MSG_LIMITE
    try:
        dados = ai.extrair_dados_cadastro(church, texto)
    except Exception:
        logger.exception("Falha ao extrair dados de cadastro via IA (igreja %s)", church.pk)
        return 'Não consegui entender agora — pode tentar mandar de novo? Ou digite "2" pra falar com a secretaria.'

    if not dados.get("full_name"):
        return "Não consegui identificar seu nome completo nessa mensagem — pode mandar de novo, começando pelo nome?"

    conversation.state = Conversation.State.AGUARDANDO_CONFIRMACAO
    conversation.state_data = {"draft": dados}
    return _texto_confirmacao(dados)


def _texto_confirmacao(dados):
    linhas = [f"{_ROTULOS_CAMPO.get(campo, campo)}: {valor}" for campo, valor in dados.items()]
    return "Entendi:\n" + "\n".join(linhas) + "\n\nConfirma? (sim/não)"


def _tratar_confirmacao(church, conversation, texto):
    resposta = texto.strip().lower().rstrip(".")
    if resposta in ("sim", "s", "confirmo", "confirmar", "ok", "certo"):
        dados = dict(conversation.state_data.get("draft") or {})
        dados.setdefault("phone", conversation.phone)  # já é o próprio número — não precisa perguntar de novo
        draft = PersonDraft.objects.create(
            church=church, person=conversation.person, conversation=conversation,
            origin=PersonDraft.Origin.WHATSAPP_IA, data=dados,
        )
        alerts.avisar_novo_draft(draft)
        conversation.state = Conversation.State.MENU
        conversation.state_data = {}
        return 'Recebi! A secretaria vai revisar e confirmar em breve. Digite "menu" quando quiser outra coisa.'
    if resposta in ("não", "nao", "n"):
        conversation.state = Conversation.State.COLETANDO_CADASTRO
        conversation.state_data = {}
        return "Sem problema — pode mandar os dados de novo, do jeito que preferir."
    return "Não entendi — confirma os dados que te mandei? (sim/não)"


def _tratar_ia_livre(church, conversation, texto):
    # "menu"/"0" já são tratados globalmente em `_despachar` antes de
    # chegar aqui — não precisa repetir.
    if not ratelimit.ia_call_permitida(church, conversation.phone):
        return _MSG_LIMITE
    try:
        return ai.gerar_resposta(church, conversation, texto)
    except Exception:
        logger.exception("Falha ao chamar IA (igreja %s)", church.pk)
        return _MSG_FALHA_IA


def _tratar_aguardando_humano(church, conversation, texto):
    # "menu"/"0" já tratados globalmente em `_despachar` — chegando
    # aqui é qualquer OUTRA coisa, e o bot fica quieto de propósito:
    # secretaria responde por fora, manualmente.
    return None


def _responder(church, instance, conversation, texto_resposta):
    whatsapp.enviar_whatsapp(conversation.phone, texto_resposta, church_config=church, instance=instance)
    ConversationMessage.objects.create(
        church=church, conversation=conversation, direction=ConversationMessage.Direction.OUT, body=texto_resposta,
    )


def _esta_expirada(conversation):
    return timezone.now() - conversation.last_message_at > timedelta(hours=EXPIRACAO_HORAS)


def _buscar_person_por_telefone(phone_normalizado):
    """Mesma normalização/estratégia de `people.views._find_duplicate` —
    dígitos só, comparação em Python (telefone não é indexado/único no
    banco). Roda dentro do `Person.objects` já filtrado pela igreja
    atual (tenant_context ativo desde o webhook)."""
    from people.models import Person

    for candidata in Person.objects.exclude(phone=""):
        if _normalizar_telefone(candidata.phone) == phone_normalizado:
            return candidata
    return None


def _normalizar_telefone(raw):
    """Mesma normalização de `Person.whatsapp_number`/
    `notifications.views.normalize_phone` — duplicada aqui de propósito
    (função pura de 5 linhas, sem estado) pra não criar um import
    circular entre `assistant` e `notifications.views`."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits
