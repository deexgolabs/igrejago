from django import forms

from core.models import Church
from notifications.models import MessageTemplate
from people.models import Person

DATETIME_INPUT = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class WhatsAppProviderForm(forms.ModelForm):
    """Escolha de canal na própria tela de Conectar WhatsApp (não em
    Configurações — é operacional/sensível, mesmo espírito de já ficar
    numa tela separada de sempre). Os 2 campos da Meta só fazem sentido
    quando o provider é META_CLOUD, mas ficam sempre no form — bloquear
    isso é responsabilidade do template (mostra/esconde), não do form."""

    class Meta:
        model = Church
        fields = ["whatsapp_provider", "whatsapp_meta_phone_number_id", "whatsapp_meta_access_token"]


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

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # `Person` é `TenantModel` — mesmo motivo do `PersonForm`: o
        # queryset de um campo de formulário simples (não-ModelForm) é
        # avaliado no import do módulo (sem igreja no thread-local ainda)
        # se declarado direto na classe — por isso começa com `.none()`
        # acima e é refeito aqui, por instância.
        people_qs = Person.objects.order_by("full_name")
        # Líder de Departamento escopado só pode mandar mensagem avulsa
        # pra gente do PRÓPRIO departamento — o campo `phone` livre
        # continua existindo (mesma confiança já dada ao staff no resto
        # do sistema), só o autocomplete de pessoa cadastrada é restrito.
        if user is not None and not user.is_unrestricted_manager:
            people_qs = people_qs.filter(department__in=user.led_departments)
        self.fields["person"].queryset = people_qs

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
