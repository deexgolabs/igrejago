from django import forms

from core.models import Church
from notifications.models import MessageTemplate, WhatsAppMetaTemplate
from people.models import Person

DATETIME_INPUT = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class WhatsAppProviderForm(forms.ModelForm):
    """Escolha de canal na própria tela de Conectar WhatsApp (não em
    Configurações — é operacional/sensível, mesmo espírito de já ficar
    numa tela separada de sempre). Os campos da Meta só fazem sentido
    quando o provider é META_CLOUD, mas ficam sempre no form — bloquear
    isso é responsabilidade do template (mostra/esconde), não do form."""

    class Meta:
        model = Church
        fields = [
            "whatsapp_provider",
            "whatsapp_meta_phone_number_id",
            "whatsapp_meta_access_token",
            "whatsapp_meta_business_account_id",
        ]


BUTTON_TYPE_CHOICES = [
    ("", "— nenhum —"),
    ("QUICK_REPLY", "Resposta rápida (ex.: \"Confirmar presença\")"),
    ("URL", "Abrir link"),
    ("PHONE_NUMBER", "Ligar"),
]


class WhatsAppMetaTemplateForm(forms.ModelForm):
    """Os 3 botões não são campos do model (que guarda tudo já montado em
    `buttons`, um JSON) — são 3 conjuntos de campos soltos (tipo/texto/
    valor), reunidos em `clean()`. Evita depender de JS pra formulário
    dinâmico (nem toda "resposta rápida" tem link/telefone, e vice-versa)
    — 3 é o limite prático da própria Meta pra um template (resposta
    rápida) ou o suficiente pra 1-2 botões de link/telefone."""

    button1_type = forms.ChoiceField(label="Botão 1", choices=BUTTON_TYPE_CHOICES, required=False)
    button1_text = forms.CharField(label="Texto do botão 1", max_length=25, required=False)
    button1_value = forms.CharField(
        label="Link ou telefone do botão 1", max_length=2000, required=False,
        help_text="Preencha só para \"Abrir link\" (https://...) ou \"Ligar\" (com DDI, ex.: 5562999998888).",
    )
    button2_type = forms.ChoiceField(label="Botão 2", choices=BUTTON_TYPE_CHOICES, required=False)
    button2_text = forms.CharField(label="Texto do botão 2", max_length=25, required=False)
    button2_value = forms.CharField(label="Link ou telefone do botão 2", max_length=2000, required=False)
    button3_type = forms.ChoiceField(label="Botão 3", choices=BUTTON_TYPE_CHOICES, required=False)
    button3_text = forms.CharField(label="Texto do botão 3", max_length=25, required=False)
    button3_value = forms.CharField(label="Link ou telefone do botão 3", max_length=2000, required=False)

    class Meta:
        model = WhatsAppMetaTemplate
        fields = ["name", "language", "category", "header_text", "body_text", "footer_text"]
        widgets = {"body_text": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            for i, botao in enumerate(self.instance.buttons[:3], start=1):
                self.fields[f"button{i}_type"].initial = botao.get("type", "")
                self.fields[f"button{i}_text"].initial = botao.get("text", "")
                self.fields[f"button{i}_value"].initial = botao.get("url") or botao.get("phone_number", "")

    def clean(self):
        cleaned = super().clean()
        botoes = []
        qtd_resposta_rapida = 0
        qtd_cta = 0
        for i in range(1, 4):
            tipo = cleaned.get(f"button{i}_type")
            texto = (cleaned.get(f"button{i}_text") or "").strip()
            valor = (cleaned.get(f"button{i}_value") or "").strip()
            if not tipo:
                continue
            if not texto:
                self.add_error(f"button{i}_text", "Informe o texto do botão.")
                continue
            if tipo == "QUICK_REPLY":
                qtd_resposta_rapida += 1
                botoes.append({"type": "QUICK_REPLY", "text": texto})
            elif tipo == "URL":
                qtd_cta += 1
                if not valor:
                    self.add_error(f"button{i}_value", "Informe o link (https://...).")
                    continue
                botoes.append({"type": "URL", "text": texto, "url": valor})
            elif tipo == "PHONE_NUMBER":
                qtd_cta += 1
                if not valor:
                    self.add_error(f"button{i}_value", "Informe o telefone (com DDI, ex.: 5562999998888).")
                    continue
                botoes.append({"type": "PHONE_NUMBER", "text": texto, "phone_number": valor})
        if qtd_resposta_rapida and qtd_cta:
            raise forms.ValidationError(
                "A Meta não permite misturar botão de Resposta rápida com Link/Ligar no mesmo template — "
                "escolha um tipo só para todos os botões."
            )
        if qtd_cta > 2:
            raise forms.ValidationError("No máximo 2 botões de Link/Ligar juntos.")
        cleaned["_buttons"] = botoes
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.buttons = self.cleaned_data.get("_buttons", [])
        if commit:
            instance.save()
        return instance


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
