import uuid

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

from core.tenancy import TenantModel


class WhatsAppMessage(TenantModel):
    """Uma mensagem na fila de envio — seja de uma campanha em massa
    (`people.CampaignSendView`), um lembrete automático
    (`enviar_lembretes`) ou uma mensagem avulsa agendada por alguém da
    secretaria. Nada é enviado na hora que a mensagem é criada: quem
    realmente chama a API é o comando `processar_fila_whatsapp`, rodando
    via cron, respeitando o intervalo entre envios configurado em
    `ChurchConfig` — mandar tudo de uma vez é o jeito mais rápido de um
    número ser marcado como spam/banido pelo WhatsApp."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Aguardando"
        SENT = "SENT", "Enviada"
        FAILED = "FAILED", "Falhou"
        CANCELLED = "CANCELLED", "Cancelada"

    class DeliveryStatus(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Desconhecido"
        DELIVERED = "DELIVERED", "Entregue"
        READ = "READ", "Lida"
        FAILED = "FAILED", "Falha na entrega"

    person = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_messages",
        verbose_name="Pessoa",
    )
    phone = models.CharField(
        "Telefone", max_length=20,
        help_text="Snapshot do número no momento do envio — funciona mesmo se não vinculado a uma Pessoa.",
    )
    message = models.TextField(
        "Mensagem",
        help_text="Texto já renderizado e legível — sempre preenchido, mesmo quando enviada via "
                   "template Meta abaixo (serve de fallback pro canal Evolution, pro e-mail de "
                   "fallback e pro log/auditoria).",
    )
    meta_template = models.ForeignKey(
        "notifications.WhatsAppMetaTemplate", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="mensagens_enviadas",
        verbose_name="Template Meta (opcional)",
        help_text="Preenchido só quando o envio deve tentar usar um template aprovado da API oficial "
                   "da Meta — fora da janela de 24h, é a única forma de mandar mensagem por lá. Se o "
                   "template não estiver mais aprovado na hora do envio, cai pro texto livre acima.",
    )
    meta_template_values = models.JSONField(
        "Valores das variáveis do template", default=list, blank=True,
        help_text="Lista na ordem {{1}}, {{2}}... já resolvida (ex.: {nome} já virou o nome de verdade).",
    )

    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.PENDING)
    scheduled_for = models.DateTimeField(
        "Agendada para", null=True, blank=True,
        help_text="Em branco = envia assim que a fila for processada.",
    )
    sent_at = models.DateTimeField("Enviada em", null=True, blank=True)
    error_message = models.CharField("Erro", max_length=255, blank=True)
    retry_count = models.PositiveIntegerField(
        "Tentativas", default=0,
        help_text="Quantas vezes já tentou enviar — o processador da fila para de tentar de novo "
                   "sozinho depois de ChurchConfig.whatsapp_max_retries.",
    )

    # Confirmação de entrega, via webhook da Evolution API — nunca
    # preenchido por um envio bem-sucedido em si, só por
    # WhatsAppWebhookView quando (e se) a Evolution API notificar.
    external_id = models.CharField(
        "ID na Evolution API", max_length=100, blank=True,
        help_text="Devolvido pela API no envio — usado pra casar com o evento de confirmação do webhook.",
    )
    delivery_status = models.CharField(
        "Status de entrega", max_length=10, choices=DeliveryStatus.choices, default=DeliveryStatus.UNKNOWN
    )
    delivered_at = models.DateTimeField("Entregue em", null=True, blank=True)
    read_at = models.DateTimeField("Lida em", null=True, blank=True)

    campaign_label = models.CharField(
        "Campanha", max_length=100, blank=True,
        help_text="Rótulo livre pra agrupar um envio em massa (ex.: 'Culto especial 24/08').",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_messages_created",
        verbose_name="Criada por",
    )
    created_at = models.DateTimeField("Criada em", auto_now_add=True)

    class Meta:
        verbose_name = "Mensagem de WhatsApp"
        verbose_name_plural = "Mensagens de WhatsApp"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_status_display()} → {self.phone} ({self.created_at:%d/%m/%Y})"

    @property
    def is_due(self):
        from django.utils import timezone
        return self.scheduled_for is None or self.scheduled_for <= timezone.now()


class MessageTemplate(TenantModel):
    """Modelo de mensagem reutilizável — pra não digitar a mesma coisa do
    zero toda vez numa campanha ou mensagem avulsa. Puramente texto, sem
    lógica: quem usa escolhe no formulário e o texto entra na caixa de
    mensagem, editável antes de confirmar."""

    name = models.CharField("Nome", max_length=100)
    body = models.TextField(
        "Texto", help_text="Pode usar {nome} — substituído pelo nome do destinatário no envio.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Criado por",
    )
    created_at = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Modelo de mensagem"
        verbose_name_plural = "Modelos de mensagem"
        ordering = ["name"]

    def __str__(self):
        return self.name


class PushSubscription(TenantModel):
    """Uma inscrição de notificação push do navegador (Web Push) de um
    usuário — um mesmo usuário pode ter mais de uma (celular + desktop).
    Gerada pelo `serviceWorker.pushManager.subscribe()` no navegador e
    enviada para `notifications.PushSubscribeView`; usada por
    `core.push.enviar_push_para_usuario()`."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions",
    )
    endpoint = models.URLField("Endpoint", max_length=500, unique=True)
    p256dh = models.CharField("Chave p256dh", max_length=255)
    auth = models.CharField("Chave auth", max_length=255)
    created_at = models.DateTimeField("Criada em", auto_now_add=True)

    class Meta:
        verbose_name = "Inscrição push"
        verbose_name_plural = "Inscrições push"

    def __str__(self):
        return f"Push de {self.user} ({self.created_at:%d/%m/%Y})"


class EmailMessage(TenantModel):
    """Fila de e-mail em massa — mesmo espírito de `WhatsAppMessage`
    (nada é enviado na hora que a linha é criada; quem manda de verdade
    é `processar_fila_email`, respeitando `Church.email_batch_size` por
    execução pra não estourar cota do provedor SMTP).

    `tracking_token` é o que identifica ESTA mensagem nas 3 URLs
    públicas de rastreio (`notifications.EmailOpenTrackingView`/
    `EmailClickTrackingView`/`EmailUnsubscribeView`) — mesmo espírito de
    `EscalaVoluntario.confirm_token`, um UUID por linha, nunca o `pk`
    direto (não dá pra adivinhar qual é o próximo)."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Aguardando"
        SENT = "SENT", "Enviada"
        FAILED = "FAILED", "Falhou"
        CANCELLED = "CANCELLED", "Cancelada"

    person = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="email_messages", verbose_name="Pessoa",
    )
    email = models.EmailField("E-mail", help_text="Snapshot no momento do envio.")
    subject = models.CharField("Assunto", max_length=200)
    body = models.TextField("Mensagem", help_text="Texto simples — envolvido num modelo visual simples no envio.")

    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.PENDING)
    scheduled_for = models.DateTimeField("Agendada para", null=True, blank=True)
    sent_at = models.DateTimeField("Enviada em", null=True, blank=True)
    error_message = models.CharField("Erro", max_length=255, blank=True)

    tracking_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    opened_at = models.DateTimeField("Aberto em", null=True, blank=True)
    open_count = models.PositiveIntegerField("Vezes aberto", default=0)
    clicked_at = models.DateTimeField("Clicado em", null=True, blank=True)
    click_count = models.PositiveIntegerField("Cliques", default=0)

    campaign_label = models.CharField("Campanha", max_length=100, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="email_messages_created", verbose_name="Criada por",
    )
    created_at = models.DateTimeField("Criada em", auto_now_add=True)

    class Meta:
        verbose_name = "E-mail em massa"
        verbose_name_plural = "E-mails em massa"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_status_display()} → {self.email} ({self.created_at:%d/%m/%Y})"

    @property
    def is_due(self):
        from django.utils import timezone
        return self.scheduled_for is None or self.scheduled_for <= timezone.now()


class WhatsAppMetaTemplate(TenantModel):
    """Template de mensagem da API oficial da Meta — conceito DIFERENTE
    de `MessageTemplate` (que é só um texto local reaproveitável): aqui
    o texto precisa ser SUBMETIDO e APROVADO pela própria Meta antes de
    poder ser usado fora da janela de 24h de conversa. Variáveis usam a
    sintaxe posicional da Meta (`{{1}}`, `{{2}}`...), diferente do
    `{nome}` nomeado usado nos templates locais de WhatsApp/e-mail.

    Editável só em DRAFT/REJECTED — depois de enviado pra revisão (ver
    `notifications.WhatsAppMetaTemplateSubmitView`) o texto já foi
    submetido e não pode mais ser trocado por aqui (a Meta não permite
    editar um template em análise); um template rejeitado pode ser
    ajustado e reenviado.

    Usar um template já APROVADO pra efetivamente mandar mensagem
    ainda não está integrado na fila `WhatsAppMessage`/dispatcher
    `core.whatsapp.enviar_whatsapp` — isso é um próximo passo, não
    esta funcionalidade (ver docstring de `core/whatsapp.py`)."""

    class Category(models.TextChoices):
        MARKETING = "marketing", "Marketing"
        UTILITY = "utility", "Utilidade"
        AUTHENTICATION = "authentication", "Autenticação"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Rascunho"
        PENDING = "PENDING", "Em análise pela Meta"
        APPROVED = "APPROVED", "Aprovado"
        REJECTED = "REJECTED", "Rejeitado"
        DISABLED = "DISABLED", "Desativado"

    name = models.CharField(
        "Nome", max_length=512,
        validators=[RegexValidator(r"^[a-z0-9_]+$", "Use só letras minúsculas, números e _ (exigência da Meta).")],
        help_text="Identifica o template na Meta — não pode ser alterado depois de criado.",
    )
    language = models.CharField("Idioma", max_length=10, default="pt_BR")
    category = models.CharField("Categoria", max_length=20, choices=Category.choices, default=Category.UTILITY)

    header_text = models.CharField(
        "Cabeçalho (opcional)", max_length=60, blank=True,
        help_text="Texto simples, sem variável — só a versão de texto do cabeçalho é suportada aqui.",
    )
    body_text = models.TextField(
        "Corpo da mensagem",
        help_text="Use {{1}}, {{2}}... para variáveis — sintaxe posicional da própria Meta, "
                   "diferente do {nome} usado nos templates locais de WhatsApp/e-mail.",
    )
    footer_text = models.CharField("Rodapé (opcional)", max_length=60, blank=True)
    buttons = models.JSONField(
        "Botões", default=list, blank=True,
        help_text="Até 3 botões — TODOS de resposta rápida OU até 2 de link/telefone, nunca "
                   "misturados (regra da própria Meta). Montado pelo formulário, não digitado direto.",
    )

    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.DRAFT)
    meta_template_id = models.CharField(
        "ID na Meta", max_length=100, blank=True,
        help_text="Preenchido só depois de enviado pra aprovação — usado pra consultar status/excluir.",
    )
    rejection_reason = models.TextField("Motivo da rejeição", blank=True)
    submitted_at = models.DateTimeField("Enviado pra aprovação em", null=True, blank=True)
    status_checked_at = models.DateTimeField("Status atualizado em", null=True, blank=True)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Template Meta (WhatsApp)"
        verbose_name_plural = "Templates Meta (WhatsApp)"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    @property
    def pode_editar(self):
        return self.status in (self.Status.DRAFT, self.Status.REJECTED)

    def montar_components(self):
        """Monta a lista `components` no formato que a Business
        Management API da Meta espera — sempre um `BODY`, `HEADER`/
        `FOOTER`/`BUTTONS` só se preenchidos."""
        components = []
        if self.header_text:
            components.append({"type": "HEADER", "format": "TEXT", "text": self.header_text})
        components.append({"type": "BODY", "text": self.body_text})
        if self.footer_text:
            components.append({"type": "FOOTER", "text": self.footer_text})
        if self.buttons:
            components.append({"type": "BUTTONS", "buttons": self.buttons})
        return components

    def contar_variaveis(self):
        """Quantas variáveis distintas {{1}}, {{2}}... o corpo usa — só
        pra validar no formulário de envio que a quantidade de valores
        informados bate com o template escolhido."""
        import re
        numeros = {int(n) for n in re.findall(r"\{\{\s*(\d+)\s*\}\}", self.body_text)}
        return len(numeros)

    def renderizar_preview(self, valores):
        """Troca {{1}}, {{2}}... pelos `valores` na ordem — sintaxe
        posicional da própria Meta, diferente do `.format(nome=...)`
        usado nos templates locais de WhatsApp/e-mail. Só gera um texto
        legível pra registro/fallback — o envio de verdade via Meta manda
        os valores separados, não este texto (ver `core.whatsapp
        ._enviar_via_meta_cloud`)."""
        import re

        def _substituir(match):
            indice = int(match.group(1)) - 1
            return str(valores[indice]) if 0 <= indice < len(valores) else match.group(0)

        return re.sub(r"\{\{\s*(\d+)\s*\}\}", _substituir, self.body_text)


class SMSMessage(TenantModel):
    """Fila de SMS — "preparado, não integrado" (mesmo padrão de Sentry/
    Web Push/Evolution API): a fila/tela funciona de verdade, só a
    chamada real pro provedor (`core.sms.enviar_sms`) ainda não existe
    — sem um provedor escolhido, cai sempre no fallback de log. Campos
    mais enxutos que `WhatsAppMessage` de propósito (sem `delivery_status`/
    `external_id` — isso varia demais entre provedores de SMS pra
    modelar sem ter escolhido um)."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Aguardando"
        SENT = "SENT", "Enviada"
        FAILED = "FAILED", "Falhou"
        CANCELLED = "CANCELLED", "Cancelada"

    person = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sms_messages", verbose_name="Pessoa",
    )
    phone = models.CharField("Telefone", max_length=20, help_text="Snapshot no momento do envio.")
    message = models.CharField("Mensagem", max_length=320)

    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.PENDING)
    scheduled_for = models.DateTimeField("Agendada para", null=True, blank=True)
    sent_at = models.DateTimeField("Enviada em", null=True, blank=True)
    error_message = models.CharField("Erro", max_length=255, blank=True)
    retry_count = models.PositiveIntegerField("Tentativas", default=0)

    campaign_label = models.CharField("Campanha", max_length=100, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sms_messages_created", verbose_name="Criada por",
    )
    created_at = models.DateTimeField("Criada em", auto_now_add=True)

    class Meta:
        verbose_name = "SMS"
        verbose_name_plural = "SMS"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_status_display()} → {self.phone} ({self.created_at:%d/%m/%Y})"

    @property
    def is_due(self):
        from django.utils import timezone
        return self.scheduled_for is None or self.scheduled_for <= timezone.now()
