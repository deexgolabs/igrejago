"""Envio de WhatsApp via Evolution API (gateway open-source, self-hosted,
que conecta a um número real por QR code — sem aprovação de conta
comercial da Meta) e gestão da instância (criar, obter QR code, checar
status). Mesmo formato de API já usado no crm-odonto para o envio; a parte
de gestão de instância é nova aqui. Sem `ChurchConfig.whatsapp_api_configured`,
o envio cai no fallback de imprimir no console — a fila continua
funcionando (e testável) sem nenhuma credencial real."""

import logging
import sys

import requests

logger = logging.getLogger(__name__)


def enviar_whatsapp(phone, message, *, church_config):
    """Envia UMA mensagem agora. Devolve (True, "", external_id) em
    sucesso ou (False, "motivo", "") em falha — quem processa a fila
    (`processar_fila_whatsapp`) usa o motivo pra preencher
    `WhatsAppMessage.error_message` e o `external_id` (o id da mensagem
    devolvido pela Evolution API) pra depois casar com o evento de
    confirmação de entrega que chegar no webhook. Não aplica nenhum
    intervalo/espera aqui — isso é responsabilidade de quem chama em
    loop, pra manter esta função simples e testável isoladamente."""
    if not phone:
        return False, "Sem telefone", ""

    if not church_config.whatsapp_api_configured:
        _print_safe(f"[WhatsApp — console fallback] Para {phone}: {message}")
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


def criar_instancia(church_config, *, instance_name):
    """Cria uma nova instância (conexão de WhatsApp) no servidor Evolution
    API, usando a chave GLOBAL (admin). Devolve o dict de resposta da API —
    o formato exato varia por versão da Evolution API; o código abaixo
    segue o shape documentado da v2, nunca testado contra um servidor real
    (nenhuma instância Evolution existe neste ambiente de dev). Ajuste
    `EVOLUTION_*` em `core/whatsapp.py` se seu servidor responder diferente."""
    response = requests.post(
        f"{church_config.whatsapp_api_url}/instance/create",
        json={
            "instanceName": instance_name,
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS",
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
