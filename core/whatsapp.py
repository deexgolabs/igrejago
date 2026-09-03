"""Envio de WhatsApp — dois canais possíveis por igreja
(`Church.whatsapp_provider`): Evolution API (gateway open-source,
self-hosted, conecta por QR code — sem aprovação de conta comercial da
Meta) ou a API oficial da Meta (WhatsApp Cloud API — texto livre só
funciona dentro de 24h da última mensagem que o CONTATO mandou pra
igreja; fora disso a Meta exige um template pré-aprovado por ela — ver
`_enviar_via_meta_cloud`). Sem `Church.whatsapp_api_configured`, o
envio cai no fallback de imprimir no console — a fila continua
funcionando (e testável) sem nenhuma credencial real, nos dois canais.

Gestão de template da Meta (`criar_template_meta`/
`consultar_status_template_meta`/`excluir_template_meta`, usadas por
`notifications.WhatsAppMetaTemplate*`): CRUD + submissão pra revisão da
Meta + consulta manual de status já são reais (chamadas de verdade na
Graph API). USAR um template já APROVADO pra efetivamente enviar
mensagem (integrar no dispatcher acima/na fila `WhatsAppMessage`) ainda
não está feito — próximo passo, fora desta rodada."""

import logging
import sys

import requests

from core.models import Church

logger = logging.getLogger(__name__)


def enviar_whatsapp(phone, message, *, church_config):
    """Envia UMA mensagem agora, pelo canal configurado nesta igreja.
    Devolve (True, "", external_id) em sucesso ou (False, "motivo", "")
    em falha — quem processa a fila (`processar_fila_whatsapp`) usa o
    motivo pra preencher `WhatsAppMessage.error_message` e o
    `external_id` (o id da mensagem devolvido pela API) pra depois casar
    com o evento de confirmação de entrega que chegar no webhook (hoje
    só implementado pra Evolution — ver `WhatsAppWebhookView`). Não
    aplica nenhum intervalo/espera aqui — isso é responsabilidade de
    quem chama em loop, pra manter esta função simples e testável
    isoladamente."""
    if not phone:
        return False, "Sem telefone", ""

    if church_config.whatsapp_provider == Church.WhatsAppProvider.META_CLOUD:
        return _enviar_via_meta_cloud(phone, message, church_config=church_config)
    return _enviar_via_evolution(phone, message, church_config=church_config)


def _enviar_via_evolution(phone, message, *, church_config):
    if not church_config.whatsapp_api_configured:
        _print_safe(f"[WhatsApp Evolution — console fallback] Para {phone}: {message}")
        return True, "", ""

    try:
        response = requests.post(
            f"{church_config.whatsapp_api_url}/message/sendText/{church_config.whatsapp_instance}",
            json={"number": phone, "text": message},
            headers={"apikey": church_config.whatsapp_send_key},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        external_id = data.get("key", {}).get("id", "") if isinstance(data, dict) else ""
        return True, "", external_id
    except Exception as exc:
        logger.exception("Falha ao enviar WhatsApp para %s via Evolution API", phone)
        return False, str(exc)[:255], ""


def _enviar_via_meta_cloud(phone, message, *, church_config):
    """WhatsApp Cloud API oficial da Meta — `phone`/`token` são da
    PRÓPRIA igreja (painel de desenvolvedor da Meta), não infraestrutura
    desta plataforma. Texto livre (`type: text`) só é aceito pela Meta
    dentro da janela de 24h da última mensagem que o CONTATO mandou —
    fora disso, ela recusa com um erro específico (código 131047 ou
    mensagem citando "template"), tratado aqui pra devolver um motivo
    compreensível em vez da exceção HTTP crua."""
    if not church_config.whatsapp_api_configured:
        _print_safe(f"[WhatsApp Meta Cloud — console fallback] Para {phone}: {message}")
        return True, "", ""

    try:
        response = requests.post(
            f"https://graph.facebook.com/v20.0/{church_config.whatsapp_meta_phone_number_id}/messages",
            json={"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": message}},
            headers={"Authorization": f"Bearer {church_config.whatsapp_meta_access_token}"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        external_id = data.get("messages", [{}])[0].get("id", "") if isinstance(data, dict) else ""
        return True, "", external_id
    except requests.HTTPError as exc:
        detalhe = str(exc)
        try:
            erro_meta = exc.response.json().get("error", {}).get("message", "")
        except Exception:
            erro_meta = ""
        if "template" in erro_meta.lower() or "24" in erro_meta:
            detalhe = (
                "A Meta recusou texto livre fora da janela de 24h da última mensagem do contato — "
                "fora dessa janela ela exige um template pré-aprovado, ainda não suportado aqui."
            )
        elif erro_meta:
            detalhe = erro_meta
        logger.exception("Falha ao enviar WhatsApp para %s via Meta Cloud API", phone)
        return False, detalhe[:255], ""
    except Exception as exc:
        logger.exception("Falha ao enviar WhatsApp para %s via Meta Cloud API", phone)
        return False, str(exc)[:255], ""


GRAPH_API_VERSION = "v23.0"


def criar_template_meta(*, waba_id, access_token, name, language, category, components):
    """Submete um template pra revisão da Meta (Business Management API
    — escopado ao WABA id, credencial diferente do `phone_number_id`
    usado só pra enviar mensagem). `category` já deve vir minúscula
    ("marketing"/"utility"/"authentication" — convenção da própria
    Meta); `components` é a lista completa (`[{"type": "BODY", ...},
    ...]`) já montada por quem chama. Devolve o JSON cru da resposta
    (`{"id": ..., "status": ...}`) ou levanta a exceção HTTP — quem
    chama decide como tratar erro (mesmo espírito de
    `_enviar_via_meta_cloud`, que já interpreta a mensagem de erro da
    Meta pra dar feedback legível)."""
    response = requests.post(
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/{waba_id}/message_templates",
        json={"name": name, "language": language, "category": category, "components": components},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def consultar_status_template_meta(*, access_token, template_id):
    """Reconsulta o status direto na Graph API — nunca confiar em cache
    local pra decidir se um template já foi aprovado (mesmo princípio
    de `core.mercadopago_billing.consultar_assinatura`: sem webhook de
    status configurado ainda, quem quiser saber o estado real clica
    "Atualizar status" e isso chama aqui)."""
    response = requests.get(
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/{template_id}",
        params={"fields": "status,rejected_reason"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def excluir_template_meta(*, waba_id, access_token, name):
    """Exclui o template também do lado da Meta (por nome — é assim que
    o endpoint de delete da Business Management API funciona, não por
    id). Quem chama trata falha como best-effort — a exclusão local do
    registro acontece de qualquer jeito (ver
    `WhatsAppMetaTemplateDeleteView`)."""
    response = requests.delete(
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/{waba_id}/message_templates",
        params={"name": name},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def criar_instancia(church_config, *, instance_name, webhook_url=None, webhook_secret=None):
    """Cria uma nova instância (conexão de WhatsApp) no servidor Evolution
    API, usando a chave GLOBAL (admin). Devolve o dict de resposta da API.
    Formato confirmado ao vivo contra um servidor Evolution v2.3.7 real
    (Contabo) — `hash` (chave da instância) vem como STRING direto, não
    aninhado; `qrcode.base64` já vem com o prefixo `data:image/...`.

    `webhook_url`/`webhook_secret`: quando os dois vêm preenchidos, já
    embute a configuração do webhook de confirmação de entrega na própria
    chamada de criação (`/instance/create` aceita um objeto `webhook`
    dentro do corpo — testado, funciona, evita uma segunda chamada
    separada a `/webhook/set/{instance}`). O cabeçalho `X-Webhook-Secret`
    é o que `notifications.WhatsAppWebhookView` confere pra saber de qual
    igreja é o evento — ver `core.models.Church.whatsapp_webhook_secret`."""
    payload = {
        "instanceName": instance_name,
        "qrcode": True,
        "integration": "WHATSAPP-BAILEYS",
    }
    if webhook_url and webhook_secret:
        payload["webhook"] = {
            "enabled": True,
            "url": webhook_url,
            "byEvents": False,
            "base64": False,
            "headers": {"X-Webhook-Secret": webhook_secret},
            "events": ["MESSAGES_UPDATE"],
        }
    response = requests.post(
        f"{church_config.whatsapp_api_url}/instance/create",
        json=payload,
        headers={"apikey": church_config.whatsapp_api_key},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def configurar_webhook(church_config, *, instance_name, webhook_url, webhook_secret):
    """Configura (ou reconfigura) o webhook de uma instância JÁ EXISTENTE
    — usado como fallback quando `criar_instancia()` falha porque a
    instância já existe (a Evolution API rejeita recriar uma instância
    com o mesmo nome — HTTP 403 "already in use" — mesmo sendo isso
    inofensivo pra quem só queria reconfigurar o webhook, sem afetar a
    conexão já feita). Mesmo formato de `webhook` embutido em
    `criar_instancia()`; endpoint e formato confirmados ao vivo contra um
    servidor Evolution v2.3.7 real — diferente do embutido em
    `/instance/create`, esse endpoint EXIGE o campo `enabled` explícito
    (400 Bad Request sem ele — confirmado ao vivo)."""
    response = requests.post(
        f"{church_config.whatsapp_api_url}/webhook/set/{instance_name}",
        json={
            "webhook": {
                "enabled": True,
                "url": webhook_url,
                "byEvents": False,
                "base64": False,
                "headers": {"X-Webhook-Secret": webhook_secret},
                "events": ["MESSAGES_UPDATE"],
            }
        },
        headers={"apikey": church_config.whatsapp_api_key},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def obter_qrcode(church_config):
    """Busca o QR code (base64) pra escanear e conectar o número. Chamado
    de novo (não só uma vez na criação) porque o QR expira em minutos."""
    response = requests.get(
        f"{church_config.whatsapp_api_url}/instance/connect/{church_config.whatsapp_instance}",
        headers={"apikey": church_config.whatsapp_send_key},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def obter_status_conexao(church_config):
    """Devolve o estado da conexão — normalmente algo como "open" (conectado)
    ou "close"/"connecting". O nome exato do campo varia por versão; o
    admin (`core/admin.py::ChurchConfigAdmin`) mostra o JSON cru se o
    formato não bater com o esperado, em vez de quebrar a página."""
    response = requests.get(
        f"{church_config.whatsapp_api_url}/instance/connectionState/{church_config.whatsapp_instance}",
        headers={"apikey": church_config.whatsapp_send_key},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def desconectar_instancia(church_config):
    """Desconecta o número (logout) sem apagar a instância — dá pra
    reconectar depois só gerando um QR code novo, sem precisar recriar
    nada. Diferente de "deletar instância" (que a Evolution API também
    tem, mas não é o que a tela de Conectar/Desconectar da igreja
    oferece — deletar é uma operação mais destrutiva, deixada só pro
    dono/admin mexer direto na Evolution API se um dia precisar)."""
    response = requests.delete(
        f"{church_config.whatsapp_api_url}/instance/logout/{church_config.whatsapp_instance}",
        headers={"apikey": church_config.whatsapp_send_key},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def _print_safe(text):
    """`print()` sozinho quebra em consoles Windows/cp1252 quando a
    mensagem tem emoji (comum em templates de WhatsApp) — encoda com
    fallback antes de escrever em vez de deixar o comando inteiro
    explodir com UnicodeEncodeError por causa de um emoji no template.

    `flush=True` importa mais do que parece: sob `runserver`/cron de longa
    duração (stdout é um pipe, não um terminal), o Python bufferiza a
    saída em blocos em vez de linha a linha — sem flush explícito, uma
    campanha "enviada com sucesso" pode não aparecer no log nenhuma vez
    até o buffer encher ou o processo encerrar, mesmo a chamada tendo
    funcionado. Confirmado ao vivo: uma campanha real não apareceu no log
    do `runserver` até este fix."""
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, errors="replace").decode(encoding), flush=True)
