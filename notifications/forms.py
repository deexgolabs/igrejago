from django import forms

from notifications.models import MessageTemplate
from people.models import Person

DATETIME_INPUT = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class ScheduledMessageForm(forms.Form):
    """Mensagem avulsa: escolhe uma Pessoa já cadastrada OU digita um
    telefone direto (pra alguém sem cadastro) — pelo menos um dos dois é
    obrigatório. `scheduled_for` em branco manda assim que a fila rodar."""

    person = forms.ModelChoiceField(
        label="Pessoa (opcional)", queryset=Person.objects.none(), required=False,
    )
    phone = forms.CharField(
        label="Ou telefone direto", max_length=20, required=False,
        help_text="Preencha se a mensagem não é para alguém já cadastrado.",
    )
    message = forms.CharField(label="Mensagem", widget=forms.Textarea(attrs={"rows": 4}))
    scheduled_for = forms.DateTimeField(
        label="Agendar para (opcional) — horário de Brasília", required=False, widget=DATETIME_INPUT,
        input_formats=["%Y-%m-%dT%H:%M"],
        help_text="Em branco = entra na fila para envio imediato (respeitando o intervalo configurado). "
                   "O horário digitado é interpretado no fuso do servidor (America/Sao_Paulo).",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `Person` é `TenantModel` — mesmo motivo do `PersonForm`: o
        # queryset de um campo de formulário simples (não-ModelForm) é
        # avaliado no import do módulo (sem igreja no thread-local ainda)
        # se declarado direto na classe — por isso começa com `.none()`
        # acima e é refeito aqui, por instância.
        self.fields["person"].queryset = Person.objects.order_by("full_name")

    def clean(self):
        cleaned = super().clean()
        person = cleaned.get("person")
        phone = cleaned.get("phone", "").strip()
        if not person and not phone:
            raise forms.ValidationError("Escolha uma pessoa ou informe um telefone.")
        cleaned["phone"] = phone or (person.phone if person else "")
        if not cleaned["phone"]:
            raise forms.ValidationError("A pessoa escolhida não tem telefone cadastrado — informe um telefone direto.")
        return cleaned


class MessageTemplateForm(forms.ModelForm):
    class Meta:
        model = MessageTemplate
        fields = ["name", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 4})}
