from urllib.parse import urlparse

from django import forms
from django.contrib.auth import password_validation

from accounts.models import User
from core.models import Church, ShortLink, WebhookSubscription
from people.models import Department, Person


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
            "whatsapp_absence_template", "whatsapp_birthday_template", "whatsapp_escala_template",
            "whatsapp_send_interval_seconds", "whatsapp_batch_size", "whatsapp_max_retries",
            "admin_alert_emails",
            "pix_key", "pix_key_type", "pix_receiver_name", "pix_receiver_city",
            "mercadopago_access_token", "pagbank_token",
            "ia_provider", "ia_api_key", "ia_knowledge_base", "ia_chat_enabled",
        ]
        widgets = {
            "brand_color": forms.TextInput(attrs={"type": "color"}),
            "whatsapp_absence_template": forms.Textarea(attrs={"rows": 3}),
            "whatsapp_birthday_template": forms.Textarea(attrs={"rows": 3}),
            "whatsapp_escala_template": forms.Textarea(attrs={"rows": 3}),
            "ia_knowledge_base": forms.Textarea(attrs={"rows": 6}),
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

    def save(self, *, matriz=None):
        """`matriz`: reaproveitado por `core.views.ChurchNetworkCreateView`
        (cadastro de FILIAL, feito pelo pastor da matriz já logado) —
        `None` preserva o cadastro público de sempre (igreja
        independente, sem rede)."""
        from datetime import date, timedelta

        from django.db import transaction

        with transaction.atomic():
            church = Church.objects.create(
                name=self.cleaned_data["church_name"],
                pastor_name=self.cleaned_data["pastor_name"],
                status=Church.Status.TRIAL,
                trial_expira_em=date.today() + timedelta(days=30),
                matriz=matriz,
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


# Primeiro segmento de toda rota já registrada na raiz do site
# (`church_crm/urls.py` + `core/urls.py`) — mantida à mão de propósito
# (não dá pra descobrir isso em runtime sem já ter registrado o próprio
# catch-all do link curto, o que inverteria o problema). Escolher um
# desses como slug personalizado não quebra nada — o link curto só fica
# "morto" (a rota de verdade sempre bate primeiro) — mas é confuso pra
# quem criou, então bloqueamos aqui com uma mensagem clara. Atualize esta
# lista se um app novo ganhar uma rota na raiz.
RESERVED_SLUGS = {
    "admin", "accounts", "pessoas", "eventos", "links", "financeiro",
    "celulas", "mensagens", "formularios", "checkin", "escalas", "sermoes",
    "health", "manifest.json", "sw.js", "relatorio.pdf", "configuracoes",
    "manual", "auditoria", "cadastro-igreja", "conta-suspensa", "privacidade",
    "meus-dados", "assinatura", "gestao", "links-curtos", "static", "media",
    "favicon.ico", "api", "webhooks", "trocar-unidade", "rede",
}


class ShortLinkForm(forms.ModelForm):
    """Cadastro/edição de um `ShortLink` (`core.SettingsView`/lista de
    links curtos, e atalhos pré-preenchidos a partir de Formulários,
    Eventos e Link na Bio). `target_path` aceita colar tanto um caminho
    (`/esperanca-pontal-sul/links/links/`) quanto uma URL completa (com
    `churchcrm.redecorp.co` ou `igrejago.link` na frente) — `clean_target_path`
    corta o domínio se vier junto, pra nunca gravar um domínio errado."""

    class Meta:
        model = ShortLink
        fields = ["slug", "label", "target_path"]

    def clean_slug(self):
        slug = self.cleaned_data["slug"].lower()
        if slug in RESERVED_SLUGS:
            raise forms.ValidationError("Esse nome é reservado pelo sistema — escolha outro.")
        return slug

    def clean_target_path(self):
        value = self.cleaned_data["target_path"].strip()
        parsed = urlparse(value)
        if parsed.scheme and parsed.netloc:
            value = parsed.path or "/"
            if parsed.query:
                value = f"{value}?{parsed.query}"
        if not value.startswith("/"):
            value = f"/{value}"
        return value


class WebhookSubscriptionForm(forms.ModelForm):
    class Meta:
        model = WebhookSubscription
        fields = ["url", "event_type", "is_active"]


DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class CustomReportForm(forms.Form):
    """Relatório customizável de Pessoas — escopado a Pessoas nesta
    rodada (caso de uso citado pelo usuário: "pessoas por departamento
    X período X status"); financeiro/eventos ficam pra uma próxima
    rodada. Todos os filtros são opcionais — em branco, considera
    todo mundo."""

    TIPO_CHOICES = [
        ("", "Todos"),
        ("membro", "Só membros"),
        ("visitante", "Só visitantes"),
    ]
    AGRUPAR_CHOICES = [
        ("department", "Departamento"),
        ("role", "Cargo"),
        ("mes_cadastro", "Mês de cadastro"),
        ("faixa_etaria", "Faixa etária"),
    ]

    department = forms.ModelChoiceField(label="Departamento", queryset=Department.objects.none(), required=False)
    role = forms.ChoiceField(label="Cargo", choices=[("", "Todos")], required=False)
    tipo = forms.ChoiceField(label="Tipo", choices=TIPO_CHOICES, required=False)
    data_inicio = forms.DateField(label="Cadastrado a partir de", required=False, widget=DATE_INPUT, input_formats=["%Y-%m-%d"])
    data_fim = forms.DateField(label="Cadastrado até", required=False, widget=DATE_INPUT, input_formats=["%Y-%m-%d"])
    agrupar_por = forms.ChoiceField(label="Agrupar por", choices=AGRUPAR_CHOICES, initial="department")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `Department`/`Person` são `TenantModel` — mesmo motivo de
        # sempre, queryset refeito por instância.
        self.fields["department"].queryset = Department.objects.order_by("name")
        self.fields["role"].choices = [("", "Todos")] + list(Person.Role.choices)
