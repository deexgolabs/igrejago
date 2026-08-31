from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from core.tenancy import TenantModel

# As 27 UFs — usado só pra renderizar um <select> decente no campo tipo
# "Estado (UF)"; não é FK de nada, é só uma lista fixa de siglas.
BRAZILIAN_STATES = [
    ("AC", "AC"), ("AL", "AL"), ("AP", "AP"), ("AM", "AM"), ("BA", "BA"),
    ("CE", "CE"), ("DF", "DF"), ("ES", "ES"), ("GO", "GO"), ("MA", "MA"),
    ("MT", "MT"), ("MS", "MS"), ("MG", "MG"), ("PA", "PA"), ("PB", "PB"),
    ("PR", "PR"), ("PE", "PE"), ("PI", "PI"), ("RJ", "RJ"), ("RN", "RN"),
    ("RS", "RS"), ("RO", "RO"), ("RR", "RR"), ("SC", "SC"), ("SP", "SP"),
    ("SE", "SE"), ("TO", "TO"),
]


class CustomForm(TenantModel):
    """Formulário customizável — a igreja monta os campos que quiser
    (inscrição de batismo, pesquisa, pedido de oração etc.) sem precisar
    de código novo. O disparo de WhatsApp pra quem responde é OPCIONAL
    (`send_whatsapp_confirmation`): só liga se um dos campos estiver
    marcado como "é o telefone" (`FormField.is_phone_field`) — sem isso
    não tem pra quem mandar, e a tela de edição bloqueia a combinação."""

    title = models.CharField("Título", max_length=200)
    slug = models.SlugField("Slug", max_length=220, blank=True)
    description = models.TextField("Descrição", blank=True)
    is_active = models.BooleanField(
        "Aceitando respostas", default=True,
        help_text="Desligue para fechar o formulário sem apagar as respostas já recebidas.",
    )

    send_whatsapp_confirmation = models.BooleanField(
        "Disparar mensagem de WhatsApp ao responder", default=False,
        help_text="Opcional — exige um campo marcado como \"telefone\" abaixo, em Campos.",
    )
    whatsapp_message_template = models.TextField(
        "Mensagem de confirmação", blank=True,
        default='Obrigado {nome}! Recebemos sua resposta para "{formulario}".',
        help_text="Use {nome} (campo marcado como nome) e {formulario} (título deste formulário).",
    )

    sync_to_person = models.BooleanField(
        "Criar/atualizar cadastro de pessoa automaticamente", default=False,
        help_text="Opcional — usa os campos do tipo Nome, Telefone, E-mail, Data de nascimento etc. "
                   "pra achar (por telefone, ou pelo login de quem respondeu) ou criar a pessoa no cadastro.",
    )
    notify_staff_emails = models.CharField(
        "Avisar por e-mail quando chegar resposta", max_length=500, blank=True,
        help_text="Opcional — um ou mais e-mails separados por vírgula. Deixe em branco pra não avisar ninguém.",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custom_forms_created",
        verbose_name="Criado por",
    )
    created_at = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Formulário"
        verbose_name_plural = "Formulários"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["church", "slug"], name="unique_customform_slug_per_church"),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)
            slug = base
            suffix = 1
            while CustomForm.objects.filter(church=self.church, slug=slug).exclude(pk=self.pk).exists():
                suffix += 1
                slug = f"{base}-{suffix}"
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("custom_forms_public:public", args=[self.church.slug, self.slug])

    @property
    def phone_field(self):
        return self.fields.filter(is_phone_field=True).first()

    @property
    def name_field(self):
        return self.fields.filter(is_name_field=True).first()


class FormField(TenantModel):
    """Um campo (pergunta) de um `CustomForm` — renderizado e validado na
    mão em `PublicFormView` (não um `django.forms.Form` de verdade, já que
    o conjunto de campos varia por formulário e é definido em runtime,
    mesmo raciocínio de `finance.BudgetView` pra linhas de categoria)."""

    class FieldType(models.TextChoices):
        # Texto livre
        TEXT = "TEXT", "Texto curto"
        TEXTAREA = "TEXTAREA", "Texto longo"
        # Dados pessoais comuns (mesmo vocabulário de people.Person, pra um
        # formulário de cadastro/atualização de dados fazer sentido)
        NAME = "NAME", "Nome completo"
        EMAIL = "EMAIL", "E-mail"
        PHONE = "PHONE", "Telefone/WhatsApp"
        CPF = "CPF", "CPF"
        BIRTH_DATE = "BIRTH_DATE", "Data de nascimento"
        ADDRESS = "ADDRESS", "Endereço"
        CITY = "CITY", "Cidade"
        STATE = "STATE", "Estado (UF)"
        ZIP_CODE = "ZIP_CODE", "CEP"
        GENDER = "GENDER", "Sexo"
        MARITAL_STATUS = "MARITAL_STATUS", "Estado civil"
        # Genéricos
        DATE = "DATE", "Data"
        TIME = "TIME", "Horário"
        NUMBER = "NUMBER", "Número"
        URL = "URL", "Link (URL)"
        YES_NO = "YES_NO", "Sim/Não"
        FILE = "FILE", "Arquivo (anexo)"
        CHOICE = "CHOICE", "Escolha única (opções personalizadas)"
        MULTIPLE_CHOICE = "MULTIPLE_CHOICE", "Múltipla escolha (opções personalizadas)"

    form = models.ForeignKey(CustomForm, on_delete=models.CASCADE, related_name="fields", verbose_name="Formulário")
    label = models.CharField("Pergunta", max_length=200)
    field_type = models.CharField("Tipo", max_length=20, choices=FieldType.choices, default=FieldType.TEXT)
    options = models.TextField(
        "Opções", blank=True,
        help_text="Uma por linha — só usado em \"Escolha única\"/\"Múltipla escolha\".",
    )
    required = models.BooleanField("Obrigatório", default=True)
    order = models.PositiveIntegerField("Ordem", default=0)

    is_name_field = models.BooleanField(
        "Usar como {nome} na mensagem de confirmação", default=False,
        help_text="Marque em só um campo — geralmente o do nome completo.",
    )
    is_phone_field = models.BooleanField(
        "Usar como telefone (WhatsApp) para o disparo", default=False,
        help_text="Precisa estar marcado em um campo para o disparo de WhatsApp funcionar.",
    )

    class Meta:
        verbose_name = "Campo"
        verbose_name_plural = "Campos"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.label} ({self.form.title})"

    def options_list(self):
        return [line.strip() for line in self.options.splitlines() if line.strip()]

    def choices_for_render(self):
        """Opções (valor, rótulo) pra tipos com escolha fixa — os tipos
        "de dados pessoais" com um conjunto fechado (sexo, estado civil,
        sim/não, UF) usam uma lista embutida; escolha única/múltipla usa o
        que a igreja digitou em `options`. Import local (`people.models`)
        só pra evitar import circular na carga do app."""
        from people.models import Person

        if self.field_type == self.FieldType.GENDER:
            return Person.Gender.choices
        if self.field_type == self.FieldType.MARITAL_STATUS:
            return Person.MaritalStatus.choices
        if self.field_type == self.FieldType.YES_NO:
            return [("Sim", "Sim"), ("Não", "Não")]
        if self.field_type == self.FieldType.STATE:
            return BRAZILIAN_STATES
        if self.field_type in (self.FieldType.CHOICE, self.FieldType.MULTIPLE_CHOICE):
            return [(option, option) for option in self.options_list()]
        return []


class FormResponse(TenantModel):
    form = models.ForeignKey(CustomForm, on_delete=models.CASCADE, related_name="responses", verbose_name="Formulário")
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="form_responses",
        verbose_name="Pessoa",
        help_text="Preenchido automaticamente quando quem responde está logado e tem cadastro vinculado.",
    )
    submitted_at = models.DateTimeField("Enviado em", auto_now_add=True)
    privacy_consent_at = models.DateTimeField("Consentimento LGPD em", null=True, blank=True)

    class Meta:
        verbose_name = "Resposta"
        verbose_name_plural = "Respostas"
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"Resposta de {self.form.title} em {self.submitted_at:%d/%m/%Y %H:%M}"

    def answers_by_field_id(self):
        """Valor em texto puro por campo — usado onde só o texto importa
        (mensagem de confirmação, exportação). Pra exibir com suporte a
        arquivo anexado, ver `answer_objects_by_field_id`."""
        return {answer.field_id: answer.value for answer in self.answers.all()}

    def answer_objects_by_field_id(self):
        return {answer.field_id: answer for answer in self.answers.all()}


class FormAnswer(TenantModel):
    response = models.ForeignKey(FormResponse, on_delete=models.CASCADE, related_name="answers", verbose_name="Resposta")
    field = models.ForeignKey(FormField, on_delete=models.CASCADE, related_name="answers", verbose_name="Campo")
    value = models.TextField("Valor", blank=True)
    file = models.FileField(
        "Arquivo", upload_to="custom_forms/respostas/%Y/%m/", blank=True, null=True,
        help_text="Só preenchido quando o campo é do tipo \"Arquivo (anexo)\".",
    )

    class Meta:
        verbose_name = "Resposta de campo"
        verbose_name_plural = "Respostas de campo"

    def __str__(self):
        return f"{self.field.label}: {self.value[:50]}"
