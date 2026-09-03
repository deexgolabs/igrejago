"""Assistente de IA no WhatsApp: conversa/menu de atendimento, resposta
livre via IA (Gemini/ChatGPT, chave da própria igreja) e coleta de
cadastro/atualização de `Person` — sempre como RASCUNHO (`PersonDraft`),
nunca gravado direto. A igreja também pode mandar um link pessoal
(`PersonUpdateLink`) pra alguém já cadastrado atualizar os próprios
dados por um formulário público simples — mesmo destino final
(`PersonDraft` pendente de aprovação).

Ver plano em `.claude/plans/quiet-enchanting-seahorse.md` pro desenho
completo (por que não reaproveitar `notifications.WhatsAppMessage` nem
`custom_forms`, allow-list de campos, etc.)."""

import uuid

from django.conf import settings
from django.db import models

from core.tenancy import TenantModel

# Allow-list de campos que um rascunho de Pessoa pode conter — nunca
# `role`/`status`/`is_member`/`is_visitor`/`department`/`tags`/
# `pipeline_stage`/`wants_membership`/`family` (esses continuam edição
# exclusiva da secretaria dentro do sistema, mesmo depois do rascunho
# aprovado). Defesa em profundidade contra prompt injection: mesmo que
# a IA seja convencida a "extrair" um desses campos, a chave nunca
# chega a virar um `setattr` real — nem na extração (`assistant.ai`)
# nem na aprovação (`assistant.views`).
PERSON_DRAFT_ALLOWED_FIELDS = (
    "full_name", "phone", "email", "birth_date",
    "gender", "marital_status", "address", "city", "state", "zip_code",
)


class Conversation(TenantModel):
    """Uma conversa por número de telefone por igreja — o "estado" de
    onde a pessoa está no atendimento (menu, coletando cadastro,
    aguardando confirmação, conversa livre com IA, ou aguardando um
    humano)."""

    class State(models.TextChoices):
        MENU = "MENU", "Menu"
        COLETANDO_CADASTRO = "COLETANDO_CADASTRO", "Coletando cadastro"
        AGUARDANDO_CONFIRMACAO = "AGUARDANDO_CONFIRMACAO", "Aguardando confirmação"
        IA_LIVRE = "IA_LIVRE", "Conversa livre com IA"
        AGUARDANDO_HUMANO = "AGUARDANDO_HUMANO", "Aguardando atendimento humano"

    phone = models.CharField("Telefone", max_length=20, help_text="Dígitos normalizados (mesmo formato de Person.whatsapp_number).")
    instance = models.ForeignKey(
        "notifications.WhatsAppInstance", on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="Instância de WhatsApp",
        help_text="Por qual número (Evolution) essa conversa está rolando — em branco no canal Meta Cloud "
                   "(só um número por igreja) ou enquanto a instância ainda não foi resolvida.",
    )
    person = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="Pessoa",
        help_text="Preenchida quando o telefone bate com um cadastro existente — só pra personalizar a "
                   "saudação, nunca decide sozinha se um rascunho é criação ou atualização.",
    )
    state = models.CharField("Estado", max_length=25, choices=State.choices, default=State.MENU)
    state_data = models.JSONField(
        "Dados do estado", default=dict, blank=True,
        help_text="Progresso do wizard/dados extraídos em memória — nunca lido fora deste app.",
    )
    last_message_at = models.DateTimeField("Última mensagem em", auto_now=True)
    created_at = models.DateTimeField("Criada em", auto_now_add=True)

    class Meta:
        verbose_name = "Conversa"
        verbose_name_plural = "Conversas"
        ordering = ["-last_message_at"]
        constraints = [
            models.UniqueConstraint(fields=["church", "phone"], name="unique_conversation_per_phone"),
        ]

    def __str__(self):
        return f"{self.phone} ({self.get_state_display()})"


class ConversationMessage(TenantModel):
    """Transcript de uma conversa — deliberadamente NÃO é
    `notifications.WhatsAppMessage` (que é fila de SAÍDA em lote, com
    campos como `status`/`scheduled_for`/`retry_count` que não fazem
    sentido pra mensagem recebida, nem pra resposta síncrona do bot).
    A resposta do bot é enviada direto via `core.whatsapp.enviar_whatsapp`
    dentro do próprio request do webhook; só é registrada aqui pra virar
    contexto do próximo prompt de IA."""

    class Direction(models.TextChoices):
        IN = "IN", "Recebida"
        OUT = "OUT", "Enviada"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="mensagens", verbose_name="Conversa")
    direction = models.CharField("Direção", max_length=3, choices=Direction.choices)
    body = models.TextField("Texto")
    raw_payload = models.JSONField("Payload cru", default=dict, blank=True, help_text="Fragmento do webhook, só pra debug.")
    created_at = models.DateTimeField("Em", auto_now_add=True)

    class Meta:
        verbose_name = "Mensagem da conversa"
        verbose_name_plural = "Mensagens da conversa"
        ordering = ["created_at"]

    def __str__(self):
        return f"[{self.direction}] {self.body[:40]}"


class PersonDraft(TenantModel):
    """Rascunho de cadastro/atualização de `Person` — pendente de
    aprovação humana. Nunca materializa em `Person` sozinho; mesmo
    espírito de `core.DataDeletionRequest` (fila PENDING → ação humana
    → DONE), só que aqui a ação é criar/atualizar em vez de excluir."""

    class Origin(models.TextChoices):
        WHATSAPP_IA = "WHATSAPP_IA", "WhatsApp (assistente)"
        PUBLIC_FORM = "PUBLIC_FORM", "Formulário público"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendente"
        APPROVED = "APPROVED", "Aprovado"
        REJECTED = "REJECTED", "Rejeitado"

    person = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="drafts", verbose_name="Pessoa",
        help_text="Em branco = cadastro novo (visitante); preenchida = atualização de quem já existe.",
    )
    conversation = models.ForeignKey(
        Conversation, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Conversa de origem"
    )
    origin = models.CharField("Origem", max_length=15, choices=Origin.choices)
    data = models.JSONField(
        "Dados propostos",
        help_text="Só chaves da allow-list (PERSON_DRAFT_ALLOWED_FIELDS) — nunca cargo/status/departamento/etc.",
    )
    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.PENDING)
    requested_at = models.DateTimeField("Solicitado em", auto_now_add=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Processado por"
    )
    processed_at = models.DateTimeField("Processado em", null=True, blank=True)
    rejection_reason = models.TextField("Motivo da rejeição", blank=True)

    class Meta:
        verbose_name = "Cadastro pendente"
        verbose_name_plural = "Cadastros pendentes"
        ordering = ["-requested_at"]

    def __str__(self):
        nome = self.data.get("full_name") or (self.person.full_name if self.person_id else "novo cadastro")
        return f"{nome} ({self.get_status_display()})"


class PersonUpdateLink(TenantModel):
    """Link pessoal (token único, sem login) que a secretaria/pastor
    manda pra uma pessoa JÁ cadastrada atualizar os próprios dados —
    mesmo padrão de `escalas.EscalaVoluntario.confirm_token`. Não é
    single-use: continua válido pra "manter atualizado" no futuro."""

    person = models.ForeignKey("people.Person", on_delete=models.CASCADE, related_name="update_links", verbose_name="Pessoa")
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Criado por"
    )
    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    last_used_at = models.DateTimeField("Último uso em", null=True, blank=True)

    class Meta:
        verbose_name = "Link de atualização cadastral"
        verbose_name_plural = "Links de atualização cadastral"

    def __str__(self):
        return f"Link de {self.person.full_name}"
