from django.conf import settings
from django.db import models
from django.urls import reverse

from core.tenancy import TenantModel


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
    photo = models.ImageField("Foto", upload_to="people/photos/", blank=True, null=True)
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

    # LGPD: quando a própria pessoa marcou a checkbox de consentimento no
    # cadastro público (`people.PublicVisitorForm`) — em branco pra quem
    # foi cadastrado por staff (não passou pelo formulário público, então
    # não faz sentido "retroagir" um consentimento que nunca foi dado
    # nesse formulário).
    privacy_consent_at = models.DateTimeField("Consentimento LGPD em", null=True, blank=True)

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
