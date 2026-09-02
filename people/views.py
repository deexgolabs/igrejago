from datetime import date

from django.contrib import messages
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.crypto import get_random_string
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from accounts.mixins import CanManagePeopleMixin, IsChurchManagerMixin
from accounts.models import User
from core.billing import pode_adicionar_pessoa
from core.models import WebhookSubscription
from core.ratelimit import RateLimitMixin
from core.tenancy import PublicChurchMixin, TenantFormMixin
from core.webhooks import disparar_webhook
from notifications.models import EmailMessage, MessageTemplate, SMSMessage, WhatsAppMessage
from people.forms import (
    CampaignForm,
    DepartmentForm,
    EmailCampaignForm,
    FamilyForm,
    PersonForm,
    PersonImportForm,
    PersonImportFormSet,
    PersonRoleForm,
    PublicVisitorForm,
    SMSCampaignForm,
    TagForm,
)
from people.models import Department, Family, Person, Tag

AGE_BRACKETS = {
    "child": ("Criança (0-12)", 0, 12),
    "teen": ("Adolescente (13-17)", 13, 17),
    "young_adult": ("Jovem (18-29)", 18, 29),
    "adult": ("Adulto (30-59)", 30, 59),
    "senior": ("Idoso (60+)", 60, 200),
}


def _birth_date_range_for_bracket(bracket_key):
    """Converte uma faixa etária (em anos) num intervalo de `birth_date`,
    já que a idade não é armazenada — é sempre calculada a partir da data
    de nascimento (ver `Person.age`)."""
    bracket = AGE_BRACKETS.get(bracket_key)
    if not bracket:
        return None
    _, min_age, max_age = bracket
    today = date.today()
    newest_birth_date = date(today.year - min_age, today.month, today.day)
    oldest_birth_date = date(today.year - max_age - 1, today.month, today.day)
    return oldest_birth_date, newest_birth_date


def _filter_people(get_params, user=None):
    """Mesmo filtro (busca/cargo/status/faixa etária) usado pela listagem
    E pela campanha de WhatsApp em massa — extraído pra função pra não
    duplicar a lógica entre as duas views. `user` opcional: quando é um
    Líder de Departamento escopado (não Pastor/Admin), restringe ao(s)
    departamento(s) que ele lidera — sem isso um líder veria/mandaria
    mensagem pra igreja toda."""
    qs = Person.objects.select_related("department").order_by("full_name")

    if user is not None and not user.is_unrestricted_manager:
        qs = qs.filter(department__in=user.led_departments)

    search = get_params.get("q", "").strip()
    if search:
        qs = qs.filter(full_name__icontains=search)

    role = get_params.get("role", "")
    if role:
        qs = qs.filter(role=role)

    status = get_params.get("status", "")
    if status:
        qs = qs.filter(status=status)

    age_bracket = get_params.get("age_bracket", "")
    date_range = _birth_date_range_for_bracket(age_bracket)
    if date_range:
        oldest, newest = date_range
        qs = qs.filter(birth_date__gt=oldest, birth_date__lte=newest)

    return qs


class PersonListView(CanManagePeopleMixin, ListView):
    model = Person
    template_name = "people/person_list.html"
    context_object_name = "people"
    paginate_by = 25

    def get_queryset(self):
        return _filter_people(self.request.GET, self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["role_choices"] = Person.Role.choices
        context["status_choices"] = Person.Status.choices
        context["age_bracket_choices"] = [(k, v[0]) for k, v in AGE_BRACKETS.items()]
        context["current_filters"] = {
            "q": self.request.GET.get("q", ""),
            "role": self.request.GET.get("role", ""),
            "status": self.request.GET.get("status", ""),
            "age_bracket": self.request.GET.get("age_bracket", ""),
        }

        params = self.request.GET.copy()
        params.pop("page", None)
        context["querystring"] = params.urlencode()

        church_config = self.request.church
        for person in context["people"]:
            person.whatsapp_message = church_config.whatsapp_absence_template.format(
                nome=person.full_name, pastor=church_config.pastor_name or "a liderança"
            )
        return context


class PersonDetailView(LoginRequiredMixin, DetailView):
    model = Person
    template_name = "people/person_detail.html"
    context_object_name = "person"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        person = self.object
        if self.request.user.is_unrestricted_manager and hasattr(person, "user_account"):
            context["role_form"] = PersonRoleForm(initial={"role": person.user_account.role})
        return context


class PersonUpdateRoleView(IsChurchManagerMixin, View):
    """Muda o `role` (cargo de acesso) do login de uma pessoa — só
    Pastor/Secretaria (`IsChurchManagerMixin`), pra ninguém se promover
    (ou promover terceiros) sozinho. QUEM ela lidera (departamento/
    célula) continua sendo setado no cadastro do Departamento/Célula em
    si (campo `leader`, já existente) — não aqui."""

    def post(self, request, pk):
        person = get_object_or_404(Person.objects.select_related("user_account"), pk=pk)
        if not hasattr(person, "user_account"):
            messages.error(request, f"{person.full_name} ainda não tem login no sistema.")
            return redirect("people:detail", pk=pk)

        form = PersonRoleForm(request.POST)
        if form.is_valid():
            user = person.user_account
            user.role = form.cleaned_data["role"]
            user.save(update_fields=["role"])
            messages.success(request, f"Cargo de acesso de {person.full_name} atualizado.")
        else:
            messages.error(request, "Cargo inválido.")
        return redirect("people:detail", pk=pk)


class PersonCreateAccessView(CanManagePeopleMixin, View):
    """Cria o login (`accounts.User`) de uma pessoa que ainda não tem —
    fecha a parte "self-service" do vínculo Membro↔Login: a secretaria só
    cria a conta, quem escolhe a própria senha é a pessoa mesma, via um
    e-mail de redefinição de senha (reaproveita o fluxo do accounts app)."""

    def post(self, request, pk):
        person = Person.objects.select_related("user_account").get(pk=pk)

        if hasattr(person, "user_account"):
            messages.error(request, f"{person.full_name} já tem acesso ao sistema.")
            return redirect("people:detail", pk=pk)

        if not person.email:
            messages.error(request, "Cadastre um e-mail para essa pessoa antes de criar o acesso.")
            return redirect("people:detail", pk=pk)

        username = person.email
        suffix = 1
        while User.objects.filter(username=username).exists():
            suffix += 1
            username = f"{person.email}.{suffix}"

        # Senha aleatória (não "unusable") de propósito: Django's
        # `PasswordResetForm.get_users()` filtra `has_usable_password()`,
        # então um usuário com senha unusable nunca recebe o e-mail de
        # redefinição — a pessoa ficaria com conta criada mas sem jeito de
        # entrar. Uma senha aleatória que ninguém sabe (nem nós) resolve.
        user = User.objects.create_user(
            username=username, email=person.email, password=get_random_string(32),
            first_name=person.full_name.split(" ")[0],
            role=User.Role.MEMBER, person=person, church=person.church,
        )

        form = PasswordResetForm(data={"email": person.email})
        if form.is_valid():
            form.save(
                request=request,
                email_template_name="accounts/password_reset_email.html",
                subject_template_name="accounts/password_reset_subject.txt",
            )
            messages.success(
                request,
                f"Acesso criado para {person.full_name}. Enviamos um e-mail para "
                f"{person.email} definir a senha (em dev, o e-mail aparece no console do servidor).",
            )
        else:
            messages.warning(
                request,
                f"Acesso criado (usuário: {username}), mas não foi possível enviar o e-mail de senha. "
                "Peça para a pessoa usar 'Esqueci minha senha' na tela de login.",
            )
        return redirect("people:detail", pk=pk)


class PersonCreateView(TenantFormMixin, CanManagePeopleMixin, CreateView):
    model = Person
    form_class = PersonForm
    template_name = "people/person_form.html"

    def form_valid(self, form):
        if not pode_adicionar_pessoa(self.request.church):
            messages.error(
                self.request,
                "Seu plano atingiu o limite de pessoas cadastradas — assine um plano maior pra continuar.",
            )
            return self.form_invalid(form)
        form.instance.created_by = self.request.user
        user = self.request.user
        if not user.is_unrestricted_manager:
            # Líder escopado: força o departamento pro próprio, mesmo que
            # o POST tenha vindo com outro — não deixa "vazar" pessoa
            # cadastrada por ele pra fora do departamento que ele lidera.
            # `.first()` fica `None` se ele não liderar departamento
            # nenhum (não ganha nenhum acesso extra nesse caso).
            form.instance.department = user.led_departments.first()
        messages.success(self.request, "Pessoa cadastrada com sucesso.")
        response = super().form_valid(form)
        disparar_webhook(self.request.church, WebhookSubscription.EventType.PERSON_CREATED, {
            "id": self.object.pk, "full_name": self.object.full_name,
        })
        return response


class PersonUpdateView(CanManagePeopleMixin, UpdateView):
    model = Person
    form_class = PersonForm
    template_name = "people/person_form.html"

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not user.is_unrestricted_manager:
            qs = qs.filter(department__in=user.led_departments)
        return qs

    def form_valid(self, form):
        messages.success(self.request, "Cadastro atualizado com sucesso.")
        return super().form_valid(form)


class PersonDeleteView(CanManagePeopleMixin, DeleteView):
    model = Person
    template_name = "people/person_confirm_delete.html"
    success_url = reverse_lazy("people:list")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not user.is_unrestricted_manager:
            qs = qs.filter(department__in=user.led_departments)
        return qs

    def form_valid(self, form):
        messages.success(self.request, "Cadastro removido.")
        return super().form_valid(form)


class PipelineBoardView(CanManagePeopleMixin, View):
    """Quadro kanban de acompanhamento de visitante — uma coluna por
    `Person.PipelineStage`. Mover um cartão (drag-and-drop) chama
    `PipelineMoveView` via fetch(); sem JS, dá pra mover editando a pessoa
    direto (o campo "Etapa de acompanhamento" também está no formulário
    normal de cadastro)."""

    template_name = "people/pipeline_board.html"

    def get(self, request):
        columns = [
            (stage, label, Person.objects.filter(pipeline_stage=stage).order_by("full_name"))
            for stage, label in Person.PipelineStage.choices
        ]
        return render(request, self.template_name, {"columns": columns})


class PipelineMoveView(CanManagePeopleMixin, View):
    def post(self, request, pk):
        person = get_object_or_404(Person, pk=pk)
        stage = request.POST.get("stage", "")
        if stage not in Person.PipelineStage.values:
            return HttpResponseBadRequest("etapa inválida")
        person.pipeline_stage = stage
        person.save(update_fields=["pipeline_stage"])
        return HttpResponse(status=204)


class DepartmentListView(IsChurchManagerMixin, ListView):
    model = Department
    template_name = "people/department_list.html"
    context_object_name = "departments"


class DepartmentCreateView(TenantFormMixin, IsChurchManagerMixin, CreateView):
    model = Department
    form_class = DepartmentForm
    template_name = "people/department_form.html"
    success_url = reverse_lazy("people:department_list")

    def form_valid(self, form):
        messages.success(self.request, "Departamento criado.")
        return super().form_valid(form)


class DepartmentUpdateView(IsChurchManagerMixin, UpdateView):
    model = Department
    form_class = DepartmentForm
    template_name = "people/department_form.html"
    success_url = reverse_lazy("people:department_list")

    def form_valid(self, form):
        messages.success(self.request, "Departamento atualizado.")
        return super().form_valid(form)


class DepartmentDeleteView(IsChurchManagerMixin, DeleteView):
    model = Department
    template_name = "people/department_confirm_delete.html"
    success_url = reverse_lazy("people:department_list")

    def form_valid(self, form):
        messages.success(self.request, "Departamento removido.")
        return super().form_valid(form)


class FamilyListView(CanManagePeopleMixin, ListView):
    model = Family
    template_name = "people/family_list.html"
    context_object_name = "families"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = FamilyForm()
        return context

    def post(self, request):
        form = FamilyForm(request.POST)
        if form.is_valid():
            family = form.save(commit=False)
            family.church = request.church
            family.save()
            messages.success(request, "Família criada.")
            return redirect("people:family_list")
        return render(request, self.template_name, {"families": Family.objects.all(), "form": form})


class FamilyDetailView(CanManagePeopleMixin, DetailView):
    model = Family
    template_name = "people/family_detail.html"
    context_object_name = "family"


class FamilyDeleteView(CanManagePeopleMixin, DeleteView):
    model = Family
    template_name = "people/family_confirm_delete.html"
    success_url = reverse_lazy("people:family_list")

    def form_valid(self, form):
        messages.success(self.request, "Família removida (as pessoas continuam cadastradas, sem vínculo).")
        return super().form_valid(form)


class TagListView(CanManagePeopleMixin, ListView):
    model = Tag
    template_name = "people/tag_list.html"
    context_object_name = "tags"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = TagForm()
        return context

    def post(self, request):
        form = TagForm(request.POST)
        if form.is_valid():
            tag = form.save(commit=False)
            tag.church = request.church
            tag.save()
            messages.success(request, "Tag criada.")
            return redirect("people:tag_list")
        return render(request, self.template_name, {"tags": Tag.objects.all(), "form": form})


class TagDeleteView(CanManagePeopleMixin, DeleteView):
    model = Tag
    template_name = "people/tag_confirm_delete.html"
    success_url = reverse_lazy("people:tag_list")

    def form_valid(self, form):
        messages.success(self.request, "Tag removida.")
        return super().form_valid(form)


def _clean_cell(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _parse_spreadsheet(uploaded_file):
    """Lê o .csv/.xlsx enviado e devolve uma lista de dicts prontos para
    inicializar o formset de revisão — nenhuma Person é criada aqui."""
    import pandas as pd

    if uploaded_file.name.lower().endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)
    df.columns = [str(c).strip().lower() for c in df.columns]

    rows = []
    for _, row in df.iterrows():
        full_name = _clean_cell(row.get("nome"))
        if not full_name:
            continue

        birth_date = ""
        raw_birth_date = row.get("data_nascimento")
        if raw_birth_date is not None and str(raw_birth_date).lower() != "nan":
            parsed = pd.to_datetime(raw_birth_date, errors="coerce", dayfirst=True)
            if not pd.isna(parsed):
                birth_date = parsed.strftime("%Y-%m-%d")

        rows.append({
            "include": True,
            "full_name": full_name,
            "phone": _clean_cell(row.get("telefone")),
            "email": _clean_cell(row.get("email")),
            "birth_date": birth_date,
            "role": Person.Role.VISITOR,
            "status": Person.Status.VISITOR_ONLY,
        })
    return rows


def _find_duplicate(full_name, phone, *, existing_by_phone, existing_by_name):
    """Detecta se a linha da planilha provavelmente já existe no banco —
    por telefone (dígitos normalizados, já que a planilha pode vir com ou
    sem formatação) ou, na falta de telefone, por nome exato. Não é
    garantia (duas pessoas podem ter o mesmo nome), só um alerta pra
    revisão — os dicionários vêm pré-carregados pra não fazer uma query
    por linha da planilha."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits and digits in existing_by_phone:
        return existing_by_phone[digits]
    return existing_by_name.get(full_name.strip().lower())


class PersonImportView(CanManagePeopleMixin, View):
    """Importação de planilha em duas etapas: (1) upload + parse via Pandas,
    (2) revisão — cada linha vira um formulário editável (nome, telefone,
    cargo, status, incluir/pular) antes de qualquer Person ser gravada, para
    corrigir o que a planilha trouxe errado sem precisar editar depois."""

    upload_template = "people/person_import.html"
    review_template = "people/person_import_review.html"

    def get(self, request):
        return render(request, self.upload_template, {"form": PersonImportForm()})

    def post(self, request):
        if request.POST.get("step") == "review":
            return self._handle_review_submit(request)
        return self._handle_upload(request)

    def _handle_upload(self, request):
        form = PersonImportForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.upload_template, {"form": form})

        try:
            rows = _parse_spreadsheet(form.cleaned_data["file"])
        except Exception as exc:
            messages.error(request, f"Não foi possível ler a planilha: {exc}")
            return render(request, self.upload_template, {"form": PersonImportForm()})

        if not rows:
            messages.warning(request, "Nenhuma linha com nome preenchido foi encontrada na planilha.")
            return render(request, self.upload_template, {"form": PersonImportForm()})

        existing_by_phone = {}
        existing_by_name = {}
        for existing in Person.objects.all():
            digits = "".join(ch for ch in existing.phone if ch.isdigit())
            if digits:
                existing_by_phone[digits] = existing
            existing_by_name[existing.full_name.strip().lower()] = existing

        duplicates = []
        duplicate_count = 0
        for row in rows:
            match = _find_duplicate(
                row["full_name"], row["phone"],
                existing_by_phone=existing_by_phone, existing_by_name=existing_by_name,
            )
            duplicates.append(match)
            if match:
                row["include"] = False
                duplicate_count += 1

        if duplicate_count:
            messages.warning(
                request,
                f"{duplicate_count} linha(s) parecem já existir no cadastro (mesmo telefone/nome) — "
                "vieram desmarcadas, revise antes de marcar 'Importar' se quiser mesmo duplicar.",
            )

        formset = PersonImportFormSet(initial=rows)
        rows_with_matches = list(zip(formset, duplicates))
        return render(request, self.review_template, {"formset": formset, "rows_with_matches": rows_with_matches})

    def _handle_review_submit(self, request):
        formset = PersonImportFormSet(request.POST)
        if not formset.is_valid():
            messages.error(request, "Corrija os erros indicados antes de confirmar.")
            rows_with_matches = list(zip(formset, [None] * len(formset.forms)))
            return render(request, self.review_template, {"formset": formset, "rows_with_matches": rows_with_matches})

        created, skipped, blocked_by_plan = 0, 0, 0
        for row_form in formset:
            data = row_form.cleaned_data
            if not data or not data.get("include"):
                skipped += 1
                continue
            if not pode_adicionar_pessoa(request.church):
                blocked_by_plan += 1
                continue
            Person.objects.create(
                church=request.church,
                full_name=data["full_name"],
                phone=data.get("phone", ""),
                email=data.get("email", ""),
                birth_date=data.get("birth_date") or None,
                role=data["role"],
                status=data["status"],
                is_visitor=data["role"] == Person.Role.VISITOR,
                is_member=data["role"] != Person.Role.VISITOR,
                created_by=request.user,
            )
            created += 1

        message = f"Importação concluída: {created} pessoa(s) cadastrada(s), {skipped} linha(s) não importada(s)."
        if blocked_by_plan:
            message += f" {blocked_by_plan} linha(s) não importada(s) por limite do plano — assine um plano maior pra continuar."
        messages.success(request, message)
        return redirect("people:list")


class PersonImportTemplateView(CanManagePeopleMixin, View):
    """Gera um .xlsx modelo com as colunas esperadas + duas linhas de
    exemplo, para a secretaria baixar e preencher."""

    def get(self, request):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Pessoas"
        ws.append(["nome", "telefone", "email", "data_nascimento"])
        ws.append(["Maria da Silva", "62999998888", "maria@example.com", "15/03/1990"])
        ws.append(["João Pereira", "62988887777", "", "22/11/1985"])
        for column_cells in ws.columns:
            width = max(len(str(cell.value)) for cell in column_cells) + 2
            ws.column_dimensions[column_cells[0].column_letter].width = width

        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = 'attachment; filename="modelo-importacao-pessoas.xlsx"'
        wb.save(response)
        return response


class PublicVisitorSignupView(PublicChurchMixin, TenantFormMixin, RateLimitMixin, CreateView):
    """Formulário público (sem login) de cadastro de visitante / pedido de
    membresia — o link que a igreja divulga externamente (Módulo 2).
    `PublicChurchMixin` resolve `self.church` pelo slug na URL."""

    model = Person
    form_class = PublicVisitorForm
    template_name = "people/public_signup.html"
    rate_limit_key = "public_signup"
    rate_limit_max = 20
    rate_limit_window_seconds = 300

    def get_success_url(self):
        return reverse_lazy("people_public:public_signup_done", args=[self.church.slug])

    def form_valid(self, form):
        if not pode_adicionar_pessoa(self.church):
            # Mensagem genérica de propósito — quem visita a página pública
            # não precisa saber que é um limite de plano; isso é assunto
            # entre a igreja e a plataforma, resolvido em `/assinatura/`.
            messages.error(self.request, "Não foi possível concluir o cadastro agora. Tente novamente mais tarde.")
            return self.form_invalid(form)
        messages.success(self.request, "Cadastro recebido! Em breve alguém da equipe entra em contato.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["church"] = self.church
        return context


class PublicVisitorSignupDoneView(PublicChurchMixin, TemplateView):
    template_name = "people/public_signup_done.html"


class CampaignSendView(CanManagePeopleMixin, View):
    """Campanha de WhatsApp em massa: reaproveita o MESMO filtro da listagem
    (`_filter_people`) — a lista que a secretaria já filtrou em
    `/pessoas/` é quem recebe a mensagem, sem precisar selecionar pessoa
    por pessoa. NÃO envia nada na hora — cria uma `WhatsAppMessage` por
    destinatário na fila (`notifications` app); quem manda de verdade é o
    comando `processar_fila_whatsapp` via cron, respeitando o intervalo
    entre envios. Mandar uma campanha inteira de uma vez dentro de uma
    request HTTP, sem intervalo, é exatamente o tipo de coisa que faz um
    número ser banido pelo WhatsApp — por isso a fila, não um loop direto."""

    template_name = "people/campaign_form.html"

    def get(self, request):
        people = _filter_people(request.GET, request.user).exclude(phone="")
        form = CampaignForm()
        return render(request, self.template_name, {
            "form": form, "recipient_count": people.count(), "templates": MessageTemplate.objects.all(),
        })

    def post(self, request):
        people = _filter_people(request.GET, request.user).exclude(phone="")
        form = CampaignForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "recipient_count": people.count()})

        label = form.cleaned_data["campaign_label"] or f"Campanha {date.today():%d/%m/%Y}"
        queued = WhatsAppMessage.objects.bulk_create([
            WhatsAppMessage(
                church=request.church,
                person=person,
                phone=person.whatsapp_number,
                message=form.cleaned_data["message"].format(nome=person.full_name),
                campaign_label=label,
                created_by=request.user,
            )
            for person in people
        ])

        messages.success(
            request,
            f"{len(queued)} mensagem(ns) adicionada(s) à fila — serão enviadas aos poucos pelo processador da fila.",
        )
        return redirect("notifications:queue")


class EmailCampaignSendView(CanManagePeopleMixin, View):
    """Campanha de e-mail em massa — mesmo espírito de `CampaignSendView`
    (mesmo `_filter_people`, mesma fila-não-envio-direto), só trocando
    WhatsApp por e-mail e excluindo quem não tem `email` cadastrado em
    vez de `phone`."""

    template_name = "people/email_campaign_form.html"

    def get(self, request):
        people = _filter_people(request.GET, request.user).exclude(email="")
        return render(request, self.template_name, {"form": EmailCampaignForm(), "recipient_count": people.count()})

    def post(self, request):
        people = _filter_people(request.GET, request.user).exclude(email="")
        form = EmailCampaignForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "recipient_count": people.count()})

        label = form.cleaned_data["campaign_label"] or f"Campanha {date.today():%d/%m/%Y}"
        queued = EmailMessage.objects.bulk_create([
            EmailMessage(
                church=request.church, person=person, email=person.email,
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["message"].format(nome=person.full_name),
                campaign_label=label, created_by=request.user,
            )
            for person in people
        ])
        messages.success(
            request,
            f"{len(queued)} e-mail(s) adicionado(s) à fila — serão enviados aos poucos pelo processador da fila.",
        )
        return redirect("notifications:email_queue")


class SMSCampaignSendView(CanManagePeopleMixin, View):
    """Campanha de SMS em massa — mesmo espírito de `CampaignSendView`.
    Envio de verdade ainda não integrado (ver `core.sms`), mas a fila em
    si já funciona igual às outras."""

    template_name = "people/sms_campaign_form.html"

    def get(self, request):
        people = _filter_people(request.GET, request.user).exclude(phone="")
        return render(request, self.template_name, {"form": SMSCampaignForm(), "recipient_count": people.count()})

    def post(self, request):
        people = _filter_people(request.GET, request.user).exclude(phone="")
        form = SMSCampaignForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "recipient_count": people.count()})

        label = form.cleaned_data["campaign_label"] or f"Campanha {date.today():%d/%m/%Y}"
        queued = SMSMessage.objects.bulk_create([
            SMSMessage(
                church=request.church, person=person, phone=person.whatsapp_number,
                message=form.cleaned_data["message"].format(nome=person.full_name),
                campaign_label=label, created_by=request.user,
            )
            for person in people
        ])
        messages.success(
            request,
            f"{len(queued)} SMS adicionado(s) à fila — serão enviados aos poucos pelo processador da fila.",
        )
        return redirect("notifications:sms_queue")
