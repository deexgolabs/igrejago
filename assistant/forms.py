from django import forms

from people.models import Person


class PersonUpdateForm(forms.Form):
    """Formulário público (sem login, por token) que alimenta um
    `PersonDraft` — nunca liga direto na instância de `Person` real (ver
    `assistant.views.PersonUpdateFormView`). Mesma allow-list de campos
    de `assistant.models.PERSON_DRAFT_ALLOWED_FIELDS`."""

    full_name = forms.CharField(label="Nome completo", max_length=200, required=False)
    phone = forms.CharField(label="Telefone (WhatsApp)", max_length=20, required=False)
    email = forms.EmailField(label="E-mail", required=False)
    birth_date = forms.DateField(
        label="Data de nascimento", required=False,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        input_formats=["%Y-%m-%d"],
    )
    gender = forms.ChoiceField(
        label="Sexo", required=False, choices=[("", "—")] + list(Person.Gender.choices)
    )
    marital_status = forms.ChoiceField(
        label="Estado civil", required=False, choices=[("", "—")] + list(Person.MaritalStatus.choices)
    )
    address = forms.CharField(label="Endereço", max_length=255, required=False)
    city = forms.CharField(label="Cidade", max_length=100, required=False)
    state = forms.CharField(label="UF", max_length=2, required=False)
    zip_code = forms.CharField(label="CEP", max_length=9, required=False)
