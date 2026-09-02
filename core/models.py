from django.conf import settings
from django.db import models
from django.utils.text import slugify

from core.tenancy import TenantModel


class DataDeletionRequest(TenantModel):
    """Pedido de exclusão de dados feito pela própria pessoa (LGPD,
    direito de eliminação — Fase 3), pelo Portal do Membro
    (`core.views.MeusDadosView`). Nunca apaga nada sozinho: vira uma fila
    que a secretaria confirma (`core.views.DataDeletionRequestProcessView`)
    — exclusão de verdade é uma ação destrutiva demais pra ser automática
    sem revisão humana (ex.: a pessoa pode ter pendência financeira,
    compromisso assumido etc. que vale conversar antes)."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendente"
        DONE = "DONE", "Concluído"

    # `SET_NULL`, não `CASCADE`: o processamento normal desta solicitação
    # É excluir a própria `Person` — com `CASCADE` isso apagaria esta
    # linha JUNTO (o registro morreria exatamente no momento em que devia
    # provar que a exclusão foi feita). `person_name` é um snapshot pra o
    # histórico continuar legível mesmo depois da pessoa não existir mais.
    person = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="deletion_requests", verbose_name="Pessoa",
    )
    person_name = models.CharField("Nome da pessoa", max_length=200)
    requested_at = models.DateTimeField("Solicitado em", auto_now_add=True)
    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.PENDING)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Processado por"
    )
    processed_at = models.DateTimeField("Processado em", null=True, blank=True)

    class Meta:
        verbose_name = "Solicitação de exclusão de dados"
        verbose_name_plural = "Solicitações de exclusão de dados"
        ordering = ["-requested_at"]

    def __str__(self):
        return f"Exclusão de {self.person_name} ({self.get_status_display()})"


class AuditLog(TenantModel):
    """Registro de quem criou/editou/excluiu um registro nos models mais
    sensíveis (Person, Event, Transaction, Cell) — não é um histórico de
    campo-a-campo, só "quem mexeu em quê e quando", suficiente pra
    responder "quem apagou esse lançamento?" sem precisar de um app de
    auditoria completo."""

    class Action(models.TextChoices):
        CREATE = "CREATE", "Criação"
        UPDATE = "UPDATE", "Edição"
        DELETE = "DELETE", "Exclusão"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Usuário"
    )
    action = models.CharField("Ação", max_length=10, choices=Action.choices)
    model_name = models.CharField("Model", max_length=50)
    object_repr = models.CharField("Registro", max_length=255)
    object_id = models.CharField("ID do registro", max_length=50, blank=True)
    timestamp = models.DateTimeField("Quando", auto_now_add=True)

    class Meta:
        verbose_name = "Log de auditoria"
        verbose_name_plural = "Logs de auditoria"
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.get_action_display()} — {self.model_name} {self.object_repr}"


class Church(models.Model):
    """Uma igreja cliente — o "tenant" do sistema multi-igreja. Era
    `ChurchConfig`, um singleton (`pk=1`, uma instalação = uma igreja);
    agora é uma linha por igreja, e todo outro model "pertence" a uma
    (ver `core.tenancy.TenantModel`). Continua guardando a configuração
    da igreja (nome, PIX, WhatsApp, Mercado Pago) — só deixou de ser
    única.

    A conexão do WhatsApp (Evolution API) é infraestrutura de
    PLATAFORMA — um servidor só, operado pelo dono, com uma instância
    isolada por igreja (`settings.EVOLUTION_API_URL`/`EVOLUTION_API_KEY`,
    não campos aqui). Cada `Church` só guarda o nome/token DA SUA
    instância nesse servidor compartilhado."""

    class Status(models.TextChoices):
        TRIAL = "trial", "Em teste"
        ACTIVE = "ativo", "Ativo"
        SUSPENDED = "suspenso", "Suspenso"

    class Plano(models.TextChoices):
        BASICO = "basico", "Básico"
        PRO = "pro", "Pro"

    name = models.CharField("Nome da igreja", max_length=150)
    slug = models.SlugField("Slug", max_length=170, unique=True, blank=True)
    pastor_name = models.CharField("Nome do pastor", max_length=150, blank=True)
    logo = models.ImageField("Logo", upload_to="core/", blank=True, null=True)
    brand_color = models.CharField(
        "Cor de marca", max_length=7, default="#2563eb",
        help_text="Usada para gerar a paleta de cores do sistema (botões, links etc.).",
    )
    whatsapp_absence_template = models.TextField(
        "Modelo de mensagem — ausência",
        default=(
            "Olá {nome}, sentimos sua falta nos últimos cultos. "
            "O pastor {pastor} pediu para entrar em contato e saber como você está."
        ),
        help_text="Use {nome} e {pastor} como marcadores.",
    )

    # Plano/cobrança — controle manual pelo dono da plataforma por
    # enquanto (sem gateway de assinatura automático ainda); os campos de
    # gateway já ficam reservados pra quando isso for automatizado.
    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.ACTIVE)
    plano = models.CharField("Plano", max_length=20, choices=Plano.choices, blank=True)
    trial_expira_em = models.DateField("Trial expira em", null=True, blank=True)
    gateway_customer_id = models.CharField("ID do cliente no gateway", max_length=100, blank=True)
    gateway_subscription_id = models.CharField("ID da assinatura no gateway", max_length=100, blank=True)
    email_confirmed = models.BooleanField(
        "E-mail confirmado", default=False,
        help_text="Confirmado pelo link enviado no cadastro público (core.ChurchSignupView). "
                   "Enquanto falso, o envio de WhatsApp desta igreja fica bloqueado — não afeta o "
                   "resto do sistema, que já pode ser usado normalmente durante o trial.",
    )

    class PixKeyType(models.TextChoices):
        CPF_CNPJ = "CPF_CNPJ", "CPF/CNPJ"
        EMAIL = "EMAIL", "E-mail"
        PHONE = "PHONE", "Telefone"
        RANDOM = "RANDOM", "Chave aleatória"

    pix_key = models.CharField(
        "Chave PIX", max_length=140, blank=True,
        help_text="Usada para gerar o QR Code de pagamento de eventos pagos.",
    )
    pix_key_type = models.CharField(
        "Tipo da chave PIX", max_length=10, choices=PixKeyType.choices, blank=True
    )
    pix_receiver_name = models.CharField(
        "Nome do recebedor (PIX)", max_length=25, blank=True,
        help_text="Máx. 25 caracteres — exigido pelo padrão do Banco Central.",
    )
    pix_receiver_city = models.CharField(
        "Cidade do recebedor (PIX)", max_length=15, blank=True,
        help_text="Máx. 15 caracteres — exigido pelo padrão do Banco Central.",
    )

    mercadopago_access_token = models.CharField(
        "Access Token do Mercado Pago", max_length=200, blank=True,
        help_text="Gerado no painel de desenvolvedor do Mercado Pago — habilita checkout com confirmação automática.",
    )

    # Instância desta igreja no servidor Evolution API COMPARTILHADO da
    # plataforma (URL/chave global do servidor em si vêm de
    # settings.EVOLUTION_API_URL/EVOLUTION_API_KEY, não daqui — ver
    # docstring da classe). `whatsapp_instance` é gerado sozinho a partir
    # do slug no save(), não digitado.
    whatsapp_instance = models.CharField("Nome da instância", max_length=100, blank=True)
    whatsapp_instance_token = models.CharField(
        "Chave da instância", max_length=200, blank=True,
        help_text="Preenchida automaticamente ao criar a instância pelo admin.",
    )
    whatsapp_send_interval_seconds = models.PositiveIntegerField(
        "Intervalo entre envios (segundos)", default=6,
        help_text="Espera entre uma mensagem e outra ao processar a fila — mandar tudo de uma vez é o "
                   "jeito mais rápido do WhatsApp marcar o número como spam. 5-10s é um valor seguro.",
    )
    whatsapp_batch_size = models.PositiveIntegerField(
        "Mensagens por execução da fila", default=30,
        help_text="Limite de mensagens enviadas em uma única chamada do comando "
                   "processar_fila_whatsapp — o resto fica pra próxima execução do cron.",
    )
    whatsapp_max_retries = models.PositiveIntegerField(
        "Tentativas antes de desistir", default=3,
        help_text="Quantas vezes o processador da fila tenta reenviar uma mensagem que falhou "
                   "antes de parar de tentar sozinho (ainda dá pra reenviar manualmente depois).",
    )
    whatsapp_webhook_secret = models.CharField(
        "Segredo do webhook", max_length=100, blank=True,
        help_text="A Evolution API não assina os webhooks — esse valor é conferido no cabeçalho "
                   "X-Webhook-Secret pra confirmar que a chamada é legítima. Configure o mesmo valor "
                   "no painel da sua instância Evolution.",
    )
    whatsapp_birthday_template = models.TextField(
        "Modelo de mensagem — aniversário",
        default="Feliz aniversário, {nome}! 🎉 Que Deus continue te abençoando. Um abraço, {pastor}.",
        help_text="Use {nome} e {pastor} como marcadores.",
    )

    admin_alert_emails = models.CharField(
        "E-mails de alerta administrativo", max_length=500, blank=True,
        help_text="Um ou mais e-mails separados por vírgula — avisados se o WhatsApp desconectar sozinho, "
                   "por exemplo. Deixe em branco pra não avisar ninguém.",
    )
    whatsapp_disconnect_alert_sent = models.BooleanField(
        "Já avisou sobre desconexão atual", default=False,
        help_text="Controle interno de `verificar_conexao_whatsapp` — evita mandar um e-mail novo a cada "
                   "execução do cron enquanto a mesma queda continua; zera sozinho quando reconectar.",
    )

    matriz = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="filiais",
        verbose_name="Igreja-mãe",
        help_text="Em branco = é uma matriz (ou igreja independente). Preenchido = é filial dessa igreja — "
                   "compartilha o seletor de unidade no menu, mas `status`/`plano` continuam por conta própria "
                   "(sem cobrança automática por filial; ajuste manual pelo dono da plataforma se for o caso).",
    )
    email_batch_size = models.PositiveIntegerField(
        "Lote de e-mail por execução", default=50,
        help_text="Quantos e-mails de campanha a fila manda por vez — evita estourar a cota do provedor SMTP.",
    )
    # `null=True` numa `CharField`, deliberado por exceção aqui: com
    # `unique=True` e SEM `null`, todo mundo sem chave gerada teria o
    # mesmo valor "" e a segunda igreja a salvar quebraria a constraint
    # — `NULL` é o único jeito de "várias linhas sem valor" conviver com
    # unicidade no banco.
    api_key = models.CharField(
        "Chave da API", max_length=64, blank=True, unique=True, null=True,
        help_text="Gerada em Configurações — usada por integrações externas (API de leitura) via "
                   "cabeçalho Authorization: Bearer <chave>.",
    )

    created_at = models.DateTimeField("Criada em", auto_now_add=True)

    class Meta:
        verbose_name = "Igreja"
        verbose_name_plural = "Igrejas"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._gerar_slug_unico()
        if not self.whatsapp_instance:
            self.whatsapp_instance = f"igreja-{self.slug}"
        super().save(*args, **kwargs)

    def _gerar_slug_unico(self):
        base = slugify(self.name) or "igreja"
        slug = base
        sufixo = 1
        while Church.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            sufixo += 1
            slug = f"{base}-{sufixo}"
        return slug

    def delete(self, *args, **kwargs):
        # Sem `suppress_audit_log()`, o `post_delete` de cada model
        # auditado (Person, Cell, Event...) cria um `AuditLog` NOVO desta
        # igreja enquanto o Django cascade-apaga tudo — sobra apontando
        # pra uma igreja que não existe mais e o DELETE final quebra com
        # `FOREIGN KEY constraint failed` (ver core/signals.py).
        from core.signals import suppress_audit_log

        with suppress_audit_log():
            return super().delete(*args, **kwargs)

    @property
    def esta_bloqueada(self):
        return self.status == self.Status.SUSPENDED

    @property
    def pix_configured(self):
        return bool(self.pix_key and self.pix_receiver_name and self.pix_receiver_city)

    @property
    def mercadopago_configured(self):
        return bool(self.mercadopago_access_token)

    @property
    def whatsapp_api_configured(self):
        return bool(settings.EVOLUTION_API_URL and self.whatsapp_instance and self.whatsapp_send_key)

    @property
    def whatsapp_api_url(self):
        """URL do servidor Evolution compartilhado — vem de settings, não
        é mais um campo por igreja (ver docstring da classe)."""
        return settings.EVOLUTION_API_URL

    @property
    def whatsapp_api_key(self):
        """Chave GLOBAL (admin) do servidor Evolution compartilhado — só
        usada pra CRIAR a instância desta igreja; envio/status usam
        `whatsapp_send_key` (a chave da própria instância)."""
        return settings.EVOLUTION_API_KEY

    @property
    def whatsapp_send_key(self):
        """A chave usada pra ENVIAR mensagem/checar status de uma instância
        já conectada — a da instância, se existir, senão cai pra chave
        global da plataforma."""
        return self.whatsapp_instance_token or settings.EVOLUTION_API_KEY


class ShortLink(TenantModel):
    """Link curto (`igrejago.link/<slug>`) — dá um endereço curto e
    PERSONALIZÁVEL pra qualquer página pública (bio, formulário, evento
    etc.), resolvido por `core.views.short_link_redirect`, registrado por
    ÚLTIMO nas rotas da raiz (`church_crm/urls.py`) — só entra em jogo
    quando nenhuma rota real do sistema bateu antes, então nunca disputa
    com elas. Ao contrário do resto do projeto, `slug` é único NO SISTEMA
    INTEIRO (não por igreja): o domínio curto não carrega o slug da
    igreja no caminho, só um único segmento — por isso é um
    `unique=True` de campo, não a `UniqueConstraint(["church", "slug"])`
    de sempre."""

    slug = models.SlugField(
        "Link personalizado", max_length=60, unique=True,
        help_text="A parte depois de igrejago.link/ — só letras, números e hífen.",
    )
    label = models.CharField(
        "Identificação", max_length=150,
        help_text='Só pra você reconhecer na lista (ex.: "Link da Bio", "Inscrição Batismo").',
    )
    target_path = models.CharField(
        "Destino", max_length=300,
        help_text="Caminho ou link completo pra onde esse link curto deve levar.",
    )
    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    click_count = models.PositiveIntegerField("Cliques", default=0, editable=False)

    class Meta:
        verbose_name = "Link curto"
        verbose_name_plural = "Links curtos"
        ordering = ["-created_at"]

    def __str__(self):
        return self.slug

    @property
    def full_url(self):
        """URL curta completa (com o domínio espelho `igrejago.link`,
        quando configurado — mesmo padrão de `linkbio.BioPage.public_url`)."""
        domain = settings.PUBLIC_LINK_DOMAIN
        return f"{domain}/{self.slug}" if domain else f"/{self.slug}"


class WebhookSubscription(TenantModel):
    """Uma URL cadastrada pela igreja pra ser avisada (POST) quando um
    evento acontece no sistema (ex.: nova pessoa cadastrada) — o caso
    de uso mais comum é conectar no Zapier/Make/planilha, sem escrever
    código. `secret` assina o payload (HMAC-SHA256, mesma convenção de
    GitHub/Stripe) pra quem recebe conseguir confirmar que veio
    realmente daqui."""

    class EventType(models.TextChoices):
        PERSON_CREATED = "PERSON_CREATED", "Nova pessoa cadastrada"
        DONATION_RECEIVED = "DONATION_RECEIVED", "Doação recebida"
        EVENT_REGISTRATION_CREATED = "EVENT_REGISTRATION_CREATED", "Nova inscrição em evento"

    url = models.URLField("URL de destino", max_length=500)
    event_type = models.CharField("Evento", max_length=30, choices=EventType.choices)
    secret = models.CharField("Segredo (HMAC)", max_length=64, editable=False)
    is_active = models.BooleanField("Ativa", default=True)
    created_at = models.DateTimeField("Criada em", auto_now_add=True)

    class Meta:
        verbose_name = "Assinatura de webhook"
        verbose_name_plural = "Assinaturas de webhook"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_event_type_display()} → {self.url}"

    def save(self, *args, **kwargs):
        if not self.secret:
            import secrets
            self.secret = secrets.token_hex(32)
        super().save(*args, **kwargs)


class WebhookDelivery(TenantModel):
    """Log de UMA tentativa de entrega — criado como PENDING assim que o
    evento acontece (`core.webhooks.disparar_webhook`), o POST de
    verdade é feito depois por `processar_fila_webhooks` (mesmo motivo
    de sempre: não travar a request do usuário esperando a resposta de
    uma URL de terceiro)."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Aguardando"
        SENT = "SENT", "Entregue"
        FAILED = "FAILED", "Falhou"

    subscription = models.ForeignKey(
        WebhookSubscription, on_delete=models.CASCADE, related_name="deliveries", verbose_name="Assinatura",
    )
    event_type = models.CharField("Evento", max_length=30, choices=WebhookSubscription.EventType.choices)
    payload = models.JSONField("Payload")
    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.PENDING)
    response_status_code = models.PositiveIntegerField("Status HTTP da resposta", null=True, blank=True)
    attempt_count = models.PositiveIntegerField("Tentativas", default=0)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    sent_at = models.DateTimeField("Entregue em", null=True, blank=True)

    class Meta:
        verbose_name = "Entrega de webhook"
        verbose_name_plural = "Entregas de webhook"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_event_type_display()} — {self.get_status_display()}"
