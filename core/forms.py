from django import forms
from django.contrib.auth import password_validation

from accounts.models import User
from core.models import Church


class ChurchConfigForm(forms.ModelForm):
    """Configuração que a própria igreja (Pastor/Admin/Líder) edita — no
    próprio registro `Church` dela (`request.church`), não mais um
    singleton. Deliberadamente NÃO inclui os campos técnicos da conexão
    Evolution API (nome/token da instância, segredo do webhook) — esses
    são infraestrutura de plataforma, só pelo Django admin
    (`ChurchAdmin`, que já exige `is_staff`). A igreja só vê
    "Conectar"/"Desconectar" em `notifications.WhatsAppConnectionView`,
    sem nenhum desses valores na tela."""

    class Meta:
        model = Church
        fields = [
            "name", "pastor_name", "logo", "brand_color",
            "whatsapp_absence_template", "whatsapp_birthday_template",
            "whatsapp_send_interval_seconds", "whatsapp_batch_size", "whatsapp_max_retries",
            "admin_alert_emails",
            "pix_key", "pix_key_type", "pix_receiver_name", "pix_receiver_city",
            "mercadopago_access_token",
        ]
        widgets = {
            "brand_color": forms.TextInput(attrs={"type": "color"}),
            "whatsapp_absence_template": forms.Textarea(attrs={"rows": 3}),
            "whatsapp_birthday_template": forms.Textarea(attrs={"rows": 3}),
        }


class ChurchOverrideForm(forms.ModelForm):
    """Ajuste manual de status/plano/trial pelo dono da plataforma
    (`core.views.GestaoChurchDetailView`) — casos de suporte (estender
    trial, reativar depois de resolver um pagamento fora do fluxo
    automático do Mercado Pago). Campos técnicos (WhatsApp/PIX/Mercado
    Pago) continuam só no Django admin — link "editar tudo" na própria
    tela, não duplicado aqui."""

    class Meta:
        model = Church
        fields = ["status", "plano", "trial_expira_em"]
        widgets = {
            # format="%Y-%m-%d" é necessário pra bater com o <input type="date">
            # do navegador — sem isso o LANGUAGE_CODE pt-br faz o Django tentar
            # formatar/parsear em dd/mm/aaaa e o campo nunca preenche/valida certo.
            "trial_expira_em": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


class ChurchSignupForm(forms.Form):
    """Cadastro público de uma igreja nova (`core.ChurchSignupView`) — não
    é `ModelForm` porque cria DOIS registros (`Church` + o primeiro
    `User`, role Pastor) numa transação só; `save()` faz isso e devolve
    os dois. `website` é um honeypot (mesmo padrão de
    `custom_forms.PublicFormView` — invisível pro olho humano via CSS no
    template, qualquer valor preenchido nele finge sucesso sem gravar)."""

    church_name = forms.CharField(label="Nome da igreja", max_length=150)
    pastor_name = forms.CharField(label="Nome do pastor/responsável", max_length=150)
    username = forms.CharField(label="Usuário", max_length=150)
    email = forms.EmailField(label="E-mail")
    password = forms.CharField(label="Senha", widget=forms.PasswordInput)
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Esse usuário já existe — escolha outro.")
        return username

    def clean_password(self):
        password = self.cleaned_data["password"]
        password_validation.validate_password(password)
        return password

    def save(self):
        from datetime import date, timedelta

        from django.db import transaction

        with transaction.atomic():
            church = Church.objects.create(
                name=self.cleaned_data["church_name"],
                pastor_name=self.cleaned_data["pastor_name"],
                status=Church.Status.TRIAL,
                trial_expira_em=date.today() + timedelta(days=30),
            )
            user = User.objects.create_user(
                username=self.cleaned_data["username"],
                email=self.cleaned_data["email"],
                password=self.cleaned_data["password"],
                first_name=self.cleaned_data["pastor_name"],
                role=User.Role.PASTOR,
                church=church,
            )
        return church, user
