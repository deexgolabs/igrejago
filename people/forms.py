from django import forms
from django.utils import timezone

from accounts.models import User
from core.lgpd import privacy_consent_label
from notifications.models import WhatsAppInstance, WhatsAppMetaTemplate
from people.models import Department, Family, Person, Tag

# LANGUAGE_CODE = 'pt-br' faz o Django esperar dd/mm/aaaa nos <input type="date">
# a menos que o formato do widget seja fixado — sem isso o valor enviado pelo
# navegador (aaaa-mm-dd, padrão do input HTML5) não é reconhecido no parse.
DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class PersonForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = [
            "full_name", "photo", "birth_date", "gender", "marital_status",
            "phone", "email", "address", "city", "state", "zip_code",
            "is_visitor", "is_member", "role", "status", "department",
            "family", "tags", "pipeline_stage",
            "member_since", "baptized", "baptism_date", "wants_membership",
            "notes", "guardian", "medical_notes",
        ]
        widgets = {
            "birth_date": DATE_INPUT,
            "member_since": DATE_INPUT,
            "baptism_date": DATE_INPUT,
            "notes": forms.Textarea(attrs={"rows": 3}),
            "medical_notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("birth_date", "member_since", "baptism_date"):
            self.fields[field_name].input_formats = ["%Y-%m-%d"]
        # Opcional no formulário — sem isso, um cadastro rápido que não
        # mexe na etapa de acompanhamento falharia a validação em vez de
        # cair no default do model (`Person.PipelineStage.NEW_VISITOR`).
        self.fields["pipeline_stage"].required = False
        # `Department`/`Family`/`Tag` são `TenantModel` — o queryset padrão
        # do Django pra um FK/M2M (`fields_for_model`) é fixado quando a
        # CLASSE do form é criada (import do módulo, sem igreja nenhuma no
        # thread-local ainda), não a cada instância — sem refazer aqui,
        # mostraria departamentos/famílias/tags de TODAS as igrejas juntas.
        self.fields["department"].queryset = Department.objects.all()
        self.fields["family"].queryset = Family.objects.all()
        self.fields["tags"].queryset = Tag.objects.all()
        guardian_qs = Person.objects.all()
        if self.instance.pk:
            guardian_qs = guardian_qs.exclude(pk=self.instance.pk)
        self.fields["guardian"].queryset = guardian_qs

    def clean_pipeline_stage(self):
        return self.cleaned_data.get("pipeline_stage") or Person.PipelineStage.NEW_VISITOR


class PersonImportForm(forms.Form):
    file = forms.FileField(
        label="Planilha (.csv ou .xlsx)",
        help_text="Colunas esperadas: nome, telefone, email, data_nascimento. Baixe o modelo abaixo se tiver dúvida.",
    )


class PersonImportRowForm(forms.Form):
    """Uma linha da planilha, editável antes de confirmar a importação —
    dá para corrigir um nome mal interpretado, ajustar cargo/status ou
    desmarcar `include` para pular a linha, tudo antes de qualquer Person
    ser criado no banco."""

    include = forms.BooleanField(label="Importar", required=False, initial=True)
    full_name = forms.CharField(label="Nome completo", max_length=200)
    phone = forms.CharField(label="Telefone", max_length=20, required=False)
    email = forms.EmailField(label="E-mail", required=False)
    birth_date = forms.DateField(
        label="Nascimento", required=False, widget=DATE_INPUT, input_formats=["%Y-%m-%d"]
    )
    role = forms.ChoiceField(label="Cargo", choices=Person.Role.choices, initial=Person.Role.VISITOR)
    status = forms.ChoiceField(
        label="Status", choices=Person.Status.choices, initial=Person.Status.VISITOR_ONLY
    )


PersonImportFormSet = forms.formset_factory(PersonImportRowForm, extra=0)


class PublicVisitorForm(forms.ModelForm):
    """Formulário público (sem login) de cadastro de visitante / pedido de
    membresia — só os campos que faz sentido pedir de quem está de fora."""

    privacy_consent = forms.BooleanField(required=True)

    class Meta:
        model = Person
        fields = ["full_name", "phone", "email", "birth_date", "address", "wants_membership"]
        widgets = {"birth_date": DATE_INPUT}
        labels = {"wants_membership": "Tenho interesse em me tornar membro"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["birth_date"].input_formats = ["%Y-%m-%d"]
        self.fields["birth_date"].required = False
        self.fields["phone"].required = True
        self.fields["privacy_consent"].label = privacy_consent_label()

    def save(self, commit=True):
        person = super().save(commit=False)
        person.is_visitor = True
        person.status = Person.Status.VISITOR_ONLY
        person.role = Person.Role.VISITOR
        person.privacy_consent_at = timezone.now()
        if commit:
            person.save()
        return person


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name", "leader", "habilita_checkin"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `Person` é `TenantModel` — mesmo motivo do `PersonForm`.
        self.fields["leader"].queryset = Person.objects.all()


class PersonRoleForm(forms.Form):
    """Editar o `role` (nível de acesso) do login de uma pessoa — hoje só
    dava pra fazer isso pelo Django admin. QUEM ela lidera (departamento/
    célula) não é setado aqui — é no cadastro do Departamento/Célula, no
    campo `leader` que já existe (ver `DepartmentForm`/`cells.CellForm`)."""

    role = forms.ChoiceField(label="Cargo de acesso", choices=User.Role.choices)


class FamilyForm(forms.ModelForm):
    class Meta:
        model = Family
        fields = ["name"]


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ["name", "color"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class CampaignForm(forms.Form):
    campaign_label = forms.CharField(
        label="Rótulo da campanha", max_length=100, required=False,
        help_text="Só pra identificar esse envio na fila (ex.: 'Culto especial 24/08'). Opcional.",
    )
    message = forms.CharField(
        label="Mensagem", widget=forms.Textarea(attrs={"rows": 4}), required=False,
        help_text="Use {nome} para personalizar com o nome de cada pessoa.",
    )
    instance = forms.ModelChoiceField(
        label="Enviar por (WhatsApp)", queryset=WhatsAppInstance.objects.none(), required=False,
        help_text="Só aparece quando o canal é a Evolution API — em branco, usa a instância padrão da igreja.",
    )
    meta_template = forms.ModelChoiceField(
        label="Ou usar template aprovado da Meta (opcional)",
        queryset=WhatsAppMetaTemplate.objects.none(), required=False,
        help_text="Necessário fora da janela de 24h de conversa no canal oficial da Meta.",
    )
    meta_template_values = forms.CharField(
        label="Valores das variáveis do template (um por linha, na ordem {{1}}, {{2}}...)",
        widget=forms.Textarea(attrs={"rows": 3}), required=False,
        help_text="Pode usar {nome} — vira o nome de cada pessoa, igual a Mensagem acima.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `WhatsAppMetaTemplate`/`WhatsAppInstance` são `TenantModel` —
        # mesmo motivo de sempre, o queryset declarado na classe roda
        # sem igreja no thread-local.
        self.fields["meta_template"].queryset = WhatsAppMetaTemplate.objects.filter(
            status=WhatsAppMetaTemplate.Status.APPROVED
        )
        instances = WhatsAppInstance.objects.all()
        self.fields["instance"].queryset = instances
        instancia_padrao = instances.filter(is_default=True).first()
        if instancia_padrao:
            self.fields["instance"].initial = instancia_padrao.pk

    def clean(self):
        cleaned = super().clean()
        meta_template = cleaned.get("meta_template")
        message = (cleaned.get("message") or "").strip()
        if meta_template:
            linhas = [
                linha.strip() for linha in (cleaned.get("meta_template_values") or "").splitlines() if linha.strip()
            ]
            esperado = meta_template.contar_variaveis()
            if len(linhas) != esperado:
                raise forms.ValidationError(
                    f"Esse template usa {esperado} variável(is) — informe exatamente {esperado} "
                    f"valor(es), um por linha (você informou {len(linhas)})."
                )
            cleaned["meta_template_value_lines"] = linhas
        elif not message:
            raise forms.ValidationError("Escreva uma mensagem ou escolha um template aprovado da Meta.")
        return cleaned


class EmailCampaignForm(forms.Form):
    campaign_label = forms.CharField(
        label="Rótulo da campanha", max_length=100, required=False,
        help_text="Só pra identificar esse envio na fila. Opcional.",
    )
    subject = forms.CharField(label="Assunto", max_length=200)
    message = forms.CharField(
        label="Mensagem", widget=forms.Textarea(attrs={"rows": 6}),
        help_text="Use {nome} para personalizar com o nome de cada pessoa.",
    )


class SMSCampaignForm(forms.Form):
    campaign_label = forms.CharField(
        label="Rótulo da campanha", max_length=100, required=False,
        help_text="Só pra identificar esse envio na fila. Opcional.",
    )
    message = forms.CharField(
        label="Mensagem", max_length=320, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Use {nome} para personalizar — SMS tem limite curto (320 caracteres), sem formatação.",
    )
