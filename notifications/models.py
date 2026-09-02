import uuid

from django.conf import settings
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
    message = models.TextField("Mensagem")

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
