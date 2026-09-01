from django import forms

from checkin.models import Checkin, SalaInfantil
from people.models import Person


class SalaInfantilForm(forms.ModelForm):
    class Meta:
        model = SalaInfantil
        fields = ["name", "idade_min", "idade_max", "capacidade", "is_active"]


class CheckinForm(forms.ModelForm):
    class Meta:
        model = Checkin
        fields = ["child", "child_name", "sala", "guardian_name", "guardian_phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `Person`/`SalaInfantil` são `TenantModel` — mesmo motivo do
        # `PersonForm`: o queryset padrão do form é fixado na hora do
        # import do módulo, sem igreja nenhuma no thread-local ainda.
        self.fields["child"].required = False
        self.fields["child"].queryset = Person.objects.all()
        self.fields["child_name"].required = False
        self.fields["sala"].queryset = SalaInfantil.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        child = cleaned.get("child")
        child_name = cleaned.get("child_name")
        if not child and not child_name:
            raise forms.ValidationError("Selecione uma criança cadastrada ou digite o nome dela.")
        if child and not child_name:
            cleaned["child_name"] = child.full_name
        return cleaned
