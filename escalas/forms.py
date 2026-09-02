from django import forms

from escalas.models import Escala, EscalaVoluntario
from people.models import Department, Person

DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
TIME_INPUT = forms.TimeInput(attrs={"type": "time"})


class EscalaForm(forms.ModelForm):
    voluntarios = forms.ModelMultipleChoiceField(
        label="Voluntários", queryset=Person.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple(),
    )

    class Meta:
        model = Escala
        fields = ["department", "date", "time", "title"]
        widgets = {"date": DATE_INPUT, "time": TIME_INPUT}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].input_formats = ["%Y-%m-%d"]
        # `Department`/`Person` são `TenantModel` — mesmo motivo do `PersonForm`.
        self.fields["department"].queryset = Department.objects.all()
        self.fields["voluntarios"].queryset = Person.objects.all()
        # Líder de Departamento escopado (`user` passado pela view) só
        # pode criar/editar escala do PRÓPRIO departamento — restringe o
        # próprio <select> em vez de só confiar na checagem da view.
        if user is not None and not user.is_unrestricted_manager:
            self.fields["department"].queryset = user.led_departments
        if self.instance.pk:
            self.fields["voluntarios"].initial = self.instance.voluntarios.values_list("person_id", flat=True)


class EscalaVoluntarioResponseForm(forms.Form):
    """Sem campos — os dois botões da tela pública (confirmar/recusar)
    postam direto pra `ConfirmarEscalaView`, um valor `acao` no POST."""
