from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from core.tenancy import TenantModel
from core.uploads import random_upload_to


class Department(TenantModel):
    """Departamento/ministério (Louvor, Infantil, Diaconato etc.), usado para
    agrupar membros no dashboard do pastor (Módulo 3)."""

    name = models.CharField("Nome", max_length=100)
    leader = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_departments",
        verbose_name="Líder",
        help_text="Quem lidera esse departamento — se essa pessoa tiver login com o "
                   "cargo \"Líder de Departamento\", o acesso dela fica restrito aos "
                   "recursos deste departamento (ver accounts.User.is_department_leader).",
    )
    habilita_checkin = models.BooleanField(
        "Usa check-in infantil", default=False,
        help_text="Marque só pro departamento que atende o check-in infantil (ex.: "
                   "Ministério Infantil/Kids) — libera essa tela pro líder dele.",
    )

    class Meta:
        verbose_name = "Departamento"
        verbose_name_plural = "Departamentos"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["church", "name"], name="unique_department_name_per_church"),
        ]

    def __str__(self):
        return self.name


class Tag(TenantModel):
    """Etiqueta livre pra segmentar pessoas além dos campos fixos (cargo,
    status, departamento) — ex.: "louvor", "intercessão", "jovens"."""

    name = models.CharField("Nome", max_length=50)
    color = models.CharField("Cor", max_length=7, default="#64748b")

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["church", "name"], name="unique_tag_name_per_church"),
        ]

    def __str__(self):
        return self.name


class Family(TenantModel):
    """Agrupa pessoas com vínculo familiar (cônjuges, filhos) só pra visão
    de conjunto/relatórios — não afeta o cadastro individual de cada um."""

    name = models.CharField("Nome da família", max_length=200)

    class Meta:
        verbose_name = "Família"
        verbose_name_plural = "Famílias"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("people:family_detail", args=[self.pk])


class Person(TenantModel):
    """Cadastro único para membros e visitantes. `is_visitor`/`is_member` não
    são mutuamente exclusivos no momento do cadastro: um visitante que pede
    membresia continua marcado como visitante até a secretaria aprovar."""

    class Role(models.TextChoices):
        VISITOR = "VISITOR", "Visitante"
        MEMBER = "MEMBER", "Membro"
        DEACON = "DEACON", "Diácono"
        DEACONESS = "DEACONESS", "Diaconisa"
        ELDER = "ELDER", "Presbítero"
        MUSICIAN = "MUSICIAN", "Músico"
        DEPARTMENT_LEADER = "DEPT_LEADER", "Líder de Departamento"
        PASTOR = "PASTOR", "Pastor"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Ativo"
        INACTIVE = "INACTIVE", "Afastado"
        VISITOR_ONLY = "VISITOR", "Visitante"

    class Gender(models.TextChoices):
        MALE = "M", "Masculino"
        FEMALE = "F", "Feminino"

    class MaritalStatus(models.TextChoices):
        SINGLE = "SINGLE", "Solteiro(a)"
        MARRIED = "MARRIED", "Casado(a)"
        DIVORCED = "DIVORCED", "Divorciado(a)"
        WIDOWED = "WIDOWED", "Viúvo(a)"

    class PipelineStage(models.TextChoices):
        NEW_VISITOR = "NEW_VISITOR", "Novo visitante"
        FOLLOWING_UP = "FOLLOWING_UP", "Em acompanhamento"
        INTEGRATED = "INTEGRATED", "Integrado"
        INACTIVE = "INACTIVE", "Inativo no acompanhamento"

    # Identificação
    full_name = models.CharField("Nome completo", max_length=200)
    photo = models.ImageField("Foto", upload_to=random_upload_to("people/photos"), blank=True, null=True)
    birth_date = models.DateField("Data de nascimento", blank=True, null=True)
    gender = models.CharField("Sexo", max_length=1, choices=Gender.choices, blank=True)
    marital_status = models.CharField(
        "Estado civil", max_length=10, choices=MaritalStatus.choices, blank=True
    )

    # Contato
    phone = models.CharField(
        "Telefone (WhatsApp)",
        max_length=20,
        help_text="Formato livre; normalizado para E.164 ao gerar o link do WhatsApp.",
        blank=True,
    )
    email = models.EmailField("E-mail", blank=True)
    address = models.CharField("Endereço", max_length=255, blank=True)
    city = models.CharField("Cidade", max_length=100, blank=True)
    state = models.CharField("UF", max_length=2, blank=True)
    zip_code = models.CharField("CEP", max_length=9, blank=True)

    # Classificação eclesiástica
    is_visitor = models.BooleanField("É visitante", default=False)
    is_member = models.BooleanField("É membro", default=False)
    role = models.CharField(
        "Cargo", max_length=15, choices=Role.choices, default=Role.VISITOR
    )
    status = models.CharField(
        "Status", max_length=10, choices=Status.choices, default=Status.VISITOR_ONLY
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="members",
        verbose_name="Departamento",
    )
    family = models.ForeignKey(
        Family,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="members",
        verbose_name="Família",
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="people", verbose_name="Tags")
    pipeline_stage = models.CharField(
        "Etapa de acompanhamento", max_length=15,
        choices=PipelineStage.choices, default=PipelineStage.NEW_VISITOR,
        help_text="Pipeline de acompanhamento de visitantes (quadro em Pessoas → Acompanhamento).",
    )
    # Preenchido sozinho no save() sempre que `pipeline_stage` muda (nunca
    # editado direto) — é o que a automação de jornada (`AutomacaoJornada`)
    # usa pra saber "há quantos dias" a pessoa está nessa etapa. Fica NULL
    # pra quem nunca teve a etapa alterada desde que esse campo existe —
    # sem backfill, mesmo espírito de todo campo novo nullable no projeto.
    pipeline_stage_changed_at = models.DateTimeField(
        "Etapa mudou em", null=True, blank=True, editable=False
    )

    # Vida na igreja
    member_since = models.DateField("Membro desde", blank=True, null=True)
    baptized = models.BooleanField("Batizado", default=False)
    baptism_date = models.DateField("Data do batismo", blank=True, null=True)
    wants_membership = models.BooleanField(
        "Solicitou membresia",
        default=False,
        help_text="Marcado quando um visitante pede para se tornar membro pelo formulário público.",
    )
    notes = models.TextField("Observações", blank=True)
    guardian = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dependentes",
        verbose_name="Responsável",
        help_text="Quem busca essa pessoa — usado no check-in infantil pra saber quem pode retirar a "
                   "criança. Diferente de `family` (agrupamento solto pra relatório): este campo é "
                   "usado de verdade pelo check-in.",
    )
    medical_notes = models.TextField(
        "Restrições médicas/alergias", blank=True,
        help_text="Aparece em destaque na etiqueta impressa do check-in infantil — separado de "
                   "`notes` de propósito, pra não se perder em observações gerais.",
    )

    # LGPD: quando a própria pessoa marcou a checkbox de consentimento no
    # cadastro público (`people.PublicVisitorForm`) — em branco pra quem
    # foi cadastrado por staff (não passou pelo formulário público, então
    # não faz sentido "retroagir" um consentimento que nunca foi dado
    # nesse formulário).
    privacy_consent_at = models.DateTimeField("Consentimento LGPD em", null=True, blank=True)

    email_opted_out_at = models.DateTimeField(
        "Descadastrado de e-mail em", null=True, blank=True,
        help_text="Preenchido sozinho quando a pessoa clica \"cancelar inscrição\" num e-mail de campanha — "
                   "a partir daí ela some do público de qualquer campanha nova (ver notifications.EmailMessage).",
    )

    # Metadados
    created_at = models.DateTimeField("Cadastrado em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizado em", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="people_created",
        verbose_name="Cadastrado por",
    )

    class Meta:
        verbose_name = "Pessoa"
        verbose_name_plural = "Pessoas"
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse("people:detail", args=[self.pk])

    def save(self, *args, **kwargs):
        # Detecta mudança de `pipeline_stage` comparando com o valor que já
        # está salvo (não com o estado em memória antes desse save — cobre
        # tanto "mudou no form" quanto "mudou por update_fields solto").
        # `todas_as_igrejas` (não `objects`) de propósito: um save() pode
        # acontecer fora do contexto de tenant da própria igreja (ex.:
        # comando de manutenção rodando sem `tenant_context` ativo) — a
        # busca pelo pk é exata de qualquer forma, só queremos garantir que
        # não fica None por engano quando o manager filtrado não acha nada.
        if self.pk:
            old_stage = Person.todas_as_igrejas.filter(pk=self.pk).values_list(
                "pipeline_stage", flat=True
            ).first()
            if old_stage is not None and old_stage != self.pipeline_stage:
                self.pipeline_stage_changed_at = timezone.now()
        else:
            self.pipeline_stage_changed_at = timezone.now()
        super().save(*args, **kwargs)

    @property
    def age(self):
        if not self.birth_date:
            return None
        from datetime import date

        today = date.today()
        return today.year - self.birth_date.year - (
            (today.month, today.day) < (self.birth_date.month, self.birth_date.day)
        )

    @property
    def whatsapp_number(self):
        """Normaliza o telefone para o formato exigido pela API do WhatsApp
        (somente dígitos, com DDI 55 quando faltar)."""
        digits = "".join(ch for ch in self.phone if ch.isdigit())
        if not digits:
            return ""
        if not digits.startswith("55"):
            digits = "55" + digits
        return digits


class AutomacaoJornada(TenantModel):
    """Regra de mensagem automática por etapa do pipeline de visitantes —
    ex.: "3 dias depois de virar Novo Visitante, manda X". Processada pelo
    comando `processar_automacao_jornada` (1x/dia), que casa
    `Person.pipeline_stage_changed_at` contra `dias_depois` e enfileira em
    `WhatsAppMessage` — nunca envia direto (mesmo padrão de
    `enviar_lembretes.py`)."""

    etapa = models.CharField(
        "Etapa", max_length=15, choices=Person.PipelineStage.choices,
        help_text="Regra dispara pra quem está NESSA etapa há `dias_depois` dias.",
    )
    dias_depois = models.PositiveIntegerField(
        "Dias depois", help_text="Quantos dias depois de entrar na etapa a mensagem é enviada.",
    )
    mensagem = models.TextField(
        "Mensagem", help_text="Use {nome} pra personalizar — mesmo formato de MessageTemplate.",
    )
    ativo = models.BooleanField("Ativo", default=True)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Automação de jornada"
        verbose_name_plural = "Automações de jornada"
        ordering = ["etapa", "dias_depois"]

    def __str__(self):
        return f"{self.get_etapa_display()} + {self.dias_depois}d"
