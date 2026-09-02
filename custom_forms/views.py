import csv
import logging

from django.contrib import messages
from django.core.mail import send_mail
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView, View

from accounts.mixins import IsChurchManagerMixin
from core.lgpd import privacy_consent_label
from core.ratelimit import RateLimitMixin
from core.tenancy import PublicChurchMixin, TenantFormMixin
from custom_forms.forms import CustomFormForm, FormFieldForm
from custom_forms.models import CustomForm, FormAnswer, FormField, FormResponse
from custom_forms.starter_templates import STARTER_TEMPLATES
from notifications.models import WhatsAppMessage
from people.models import Person

logger = logging.getLogger(__name__)

# Campo do formulário → atributo de people.Person, pra sincronização
# automática (CustomForm.sync_to_person). Só os tipos com uma contrapartida
# clara em Person entram aqui — tipos "genéricos" (número, sim/não, arquivo
# etc.) nunca alimentam o cadastro.
PERSON_FIELD_MAP = {
    FormField.FieldType.NAME: "full_name",
    FormField.FieldType.EMAIL: "email",
    FormField.FieldType.PHONE: "phone",
    FormField.FieldType.BIRTH_DATE: "birth_date",
    FormField.FieldType.ADDRESS: "address",
    FormField.FieldType.CITY: "city",
    FormField.FieldType.STATE: "state",
    FormField.FieldType.ZIP_CODE: "zip_code",
    FormField.FieldType.GENDER: "gender",
    FormField.FieldType.MARITAL_STATUS: "marital_status",
}


class CustomFormListView(IsChurchManagerMixin, ListView):
    model = CustomForm
    template_name = "custom_forms/customform_list.html"
    context_object_name = "custom_forms"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["starter_choices"] = [(key, starter["title"]) for key, starter in STARTER_TEMPLATES.items()]
        return context


class CustomFormCreateView(TenantFormMixin, IsChurchManagerMixin, CreateView):
    model = CustomForm
    form_class = CustomFormForm
    template_name = "custom_forms/customform_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, "Formulário criado — agora adicione os campos abaixo.")
        response = super().form_valid(form)
        return redirect("custom_forms:field_list", pk=self.object.pk)


class CustomFormUpdateView(IsChurchManagerMixin, UpdateView):
    model = CustomForm
    form_class = CustomFormForm
    template_name = "custom_forms/customform_form.html"
    success_url = reverse_lazy("custom_forms:list")

    def form_valid(self, form):
        if form.instance.send_whatsapp_confirmation and not form.instance.phone_field:
            form.add_error(
                "send_whatsapp_confirmation",
                'Marque um campo como "telefone" na aba de Campos antes de ligar o disparo.',
            )
            return self.form_invalid(form)
        messages.success(self.request, "Formulário atualizado.")
        return super().form_valid(form)


class CustomFormDeleteView(IsChurchManagerMixin, DeleteView):
    model = CustomForm
    template_name = "custom_forms/customform_confirm_delete.html"
    success_url = reverse_lazy("custom_forms:list")

    def form_valid(self, form):
        messages.success(self.request, "Formulário removido (respostas já recebidas também são apagadas).")
        return super().form_valid(form)


class CustomFormDuplicateView(IsChurchManagerMixin, View):
    """Clona um formulário existente (config + campos, sem as respostas)
    como ponto de partida pra um novo — mais rápido do que montar tudo de
    novo quando só muda um detalhe. Sempre nasce inativo (`is_active=False`)
    pra dar tempo de revisar/ajustar antes de divulgar."""

    def post(self, request, pk):
        original = get_object_or_404(CustomForm, pk=pk)
        clone = CustomForm.objects.create(
            church=request.church,
            title=f"{original.title} (cópia)",
            description=original.description,
            is_active=False,
            send_whatsapp_confirmation=original.send_whatsapp_confirmation,
            whatsapp_message_template=original.whatsapp_message_template,
            sync_to_person=original.sync_to_person,
            notify_staff_emails=original.notify_staff_emails,
            created_by=request.user,
        )
        for field in original.fields.order_by("order", "id"):
            FormField.objects.create(
                church=request.church,
                form=clone, label=field.label, field_type=field.field_type, options=field.options,
                required=field.required, order=field.order,
                is_name_field=field.is_name_field, is_phone_field=field.is_phone_field,
            )
        messages.success(request, f'Formulário duplicado como "{clone.title}" — revise e ative quando quiser.')
        return redirect("custom_forms:field_list", pk=clone.pk)


class CustomFormFromStarterView(IsChurchManagerMixin, View):
    """Cria um formulário já com um conjunto de campos comuns pré-montado
    (ver `custom_forms.starter_templates`) — ponto de partida rápido pra
    quem não quer montar do zero. Também nasce inativo."""

    def post(self, request):
        key = request.POST.get("template", "")
        starter = STARTER_TEMPLATES.get(key)
        if not starter:
            messages.error(request, "Modelo não encontrado.")
            return redirect("custom_forms:list")

        custom_form = CustomForm.objects.create(
            church=request.church,
            title=starter["title"], is_active=False, created_by=request.user,
            **starter.get("form_defaults", {}),
        )
        for order, field_spec in enumerate(starter["fields"]):
            FormField.objects.create(church=request.church, form=custom_form, order=order, **field_spec)

        messages.success(
            request, f'Formulário "{custom_form.title}" criado a partir do modelo — revise os campos e ative quando quiser.',
        )
        return redirect("custom_forms:field_list", pk=custom_form.pk)


class FormFieldListView(IsChurchManagerMixin, View):
    """Gerencia os campos de um formulário — lista + adiciona um novo campo
    na mesma tela (mesmo padrão de `people.TagListView`/`FamilyListView`);
    editar/excluir um campo existente tem view própria."""

    template_name = "custom_forms/field_list.html"

    def get(self, request, pk):
        custom_form = get_object_or_404(CustomForm, pk=pk)
        return render(request, self.template_name, {
            "custom_form": custom_form,
            "form": FormFieldForm(initial={"order": custom_form.fields.count()}),
        })

    def post(self, request, pk):
        custom_form = get_object_or_404(CustomForm, pk=pk)
        form = FormFieldForm(request.POST)
        if form.is_valid():
            field = form.save(commit=False)
            field.form = custom_form
            field.church = custom_form.church
            field.save()
            messages.success(request, "Campo adicionado.")
            return redirect("custom_forms:field_list", pk=pk)
        return render(request, self.template_name, {"custom_form": custom_form, "form": form})


class FormFieldUpdateView(IsChurchManagerMixin, UpdateView):
    model = FormField
    form_class = FormFieldForm
    template_name = "custom_forms/field_form.html"
    pk_url_kwarg = "field_pk"

    def form_valid(self, form):
        messages.success(self.request, "Campo atualizado.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("custom_forms:field_list", args=[self.object.form_id])


class FormFieldDeleteView(IsChurchManagerMixin, View):
    def post(self, request, pk, field_pk):
        field = get_object_or_404(FormField, pk=field_pk, form_id=pk)
        field.delete()
        messages.success(request, "Campo removido.")
        return redirect("custom_forms:field_list", pk=pk)


class FormResponseListView(IsChurchManagerMixin, View):
    template_name = "custom_forms/response_list.html"

    def get(self, request, pk):
        custom_form = get_object_or_404(CustomForm, pk=pk)
        fields = list(custom_form.fields.order_by("order", "id"))
        responses = (
            custom_form.responses.select_related("person")
            .prefetch_related("answers")
            .order_by("-submitted_at")
        )
        rows = [
            {"response": response, "answers": [response.answer_objects_by_field_id().get(f.id) for f in fields]}
            for response in responses
        ]
        return render(request, self.template_name, {
            "custom_form": custom_form, "fields": fields, "rows": rows,
        })


class FormResponseExportView(IsChurchManagerMixin, View):
    def get(self, request, pk):
        custom_form = get_object_or_404(CustomForm, pk=pk)
        fields = list(custom_form.fields.order_by("order", "id"))

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="respostas-{custom_form.slug}.csv"'
        response["Cache-Control"] = "private, no-store"
        # BOM escrito manualmente uma única vez — ver nota em
        # events.RegistrationExportView sobre por que não usar
        # charset="utf-8-sig" (duplicaria o BOM a cada linha).
        response.write("﻿")

        writer = csv.writer(response)
        writer.writerow(["Enviado em"] + [f.label for f in fields])
        for form_response in custom_form.responses.prefetch_related("answers").order_by("submitted_at"):
            answers = form_response.answer_objects_by_field_id()
            row = [form_response.submitted_at.strftime("%d/%m/%Y %H:%M")]
            for f in fields:
                answer = answers.get(f.id)
                if answer and answer.file:
                    row.append(request.build_absolute_uri(answer.file.url))
                else:
                    row.append(answer.value if answer else "")
            writer.writerow(row)
        return response


class PublicFormView(PublicChurchMixin, RateLimitMixin, View):
    """Tela pública do formulário — renderizada e validada na mão a partir
    dos `FormField` cadastrados (não um `ModelForm`, já que o conjunto de
    campos é definido em runtime pela igreja)."""

    template_name = "custom_forms/public_form.html"
    rate_limit_key = "custom_form_submit"
    rate_limit_max = 20
    rate_limit_window_seconds = 300

    def get(self, request, church_slug, slug):
        custom_form = get_object_or_404(CustomForm, slug=slug, is_active=True)
        fields = custom_form.fields.order_by("order", "id")
        return render(request, self.template_name, {
            "custom_form": custom_form, "fields": fields,
            "privacy_consent_label": privacy_consent_label(),
        })

    def post(self, request, church_slug, slug):
        custom_form = get_object_or_404(CustomForm, slug=slug, is_active=True)

        # Honeypot: campo invisível pro olho humano (CSS em
        # public_form.html), que bots que preenchem tudo indiscriminadamente
        # costumam preencher sozinhos. Finge sucesso sem gravar nada — não
        # dá dica nenhuma de que foi bloqueado.
        if request.POST.get("website_confirm"):
            return redirect("custom_forms_public:public_done", church_slug=self.church.slug, slug=slug)

        fields = list(custom_form.fields.order_by("order", "id"))

        errors = {}
        cleaned = {}
        files = {}
        for field in fields:
            if field.field_type == FormField.FieldType.MULTIPLE_CHOICE:
                selected = request.POST.getlist(f"field_{field.pk}")
                cleaned[field.pk] = ", ".join(selected)
                if field.required and not selected:
                    errors[field.pk] = "Campo obrigatório."
            elif field.field_type == FormField.FieldType.FILE:
                uploaded = request.FILES.get(f"field_{field.pk}")
                files[field.pk] = uploaded
                cleaned[field.pk] = uploaded.name if uploaded else ""
                if field.required and not uploaded:
                    errors[field.pk] = "Campo obrigatório."
            else:
                raw_value = request.POST.get(f"field_{field.pk}", "").strip()
                cleaned[field.pk] = raw_value
                if field.required and not raw_value:
                    errors[field.pk] = "Campo obrigatório."

        # LGPD: checkbox fixa, fora dos `FormField` cadastrados pela
        # igreja — mesmo critério de obrigatoriedade dos outros campos.
        privacy_consent = request.POST.get("privacy_consent") == "on"
        privacy_consent_error = None if privacy_consent else "É preciso concordar com a Política de Privacidade."

        if errors or privacy_consent_error:
            # Anota erro/valor digitado direto no objeto de cada campo, em
            # memória — templates Django não conseguem indexar um dict por
            # uma variável (`errors.field.pk` não funciona), então isso é
            # mais simples do que escrever um template filter só pra isso.
            for field in fields:
                field.error = errors.get(field.pk)
                field.posted_value = cleaned.get(field.pk, "")
            return render(request, self.template_name, {
                "custom_form": custom_form, "fields": fields,
                "privacy_consent_label": privacy_consent_label(),
                "privacy_consent_error": privacy_consent_error,
            })

        logged_in_person = request.user.person if request.user.is_authenticated and request.user.person_id else None
        synced_person = logged_in_person
        if custom_form.sync_to_person:
            synced_person = self._sync_person(fields, cleaned, logged_in_person, self.church)

        form_response = FormResponse.objects.create(
            church=self.church, form=custom_form, person=synced_person,
            privacy_consent_at=timezone.now(),
        )
        # `.save()` um a um em vez de `bulk_create` — necessário pro
        # `FormAnswer.file` (FileField) ser realmente escrito no storage;
        # `bulk_create` não passa pelo `pre_save()` que faz esse trabalho,
        # então o arquivo enviado nunca seria salvo em disco. Poucas
        # respostas por envio, então o custo extra de N queries é
        # irrelevante aqui.
        for field in fields:
            if field.field_type == FormField.FieldType.FILE:
                FormAnswer.objects.create(
                    church=self.church, response=form_response, field=field, file=files.get(field.pk)
                )
            else:
                FormAnswer.objects.create(
                    church=self.church, response=form_response, field=field, value=cleaned[field.pk]
                )

        if custom_form.send_whatsapp_confirmation:
            self._queue_confirmation(custom_form, cleaned, self.church)
        if custom_form.notify_staff_emails:
            self._notify_staff(custom_form, fields, cleaned)

        return redirect("custom_forms_public:public_done", church_slug=self.church.slug, slug=slug)

    @staticmethod
    def _sync_person(fields, cleaned, existing_person, church):
        """Cria ou atualiza um `people.Person` a partir das respostas —
        só roda quando `CustomForm.sync_to_person` está ligado. Prioridade
        pra achar a pessoa: (1) quem respondeu já está logado com cadastro
        vinculado — atualiza ELE, nunca cria um duplicado; (2) acha por
        telefone (dígitos normalizados, mesmo raciocínio de
        `people.views._find_duplicate`); (3) acha por nome exato; (4) não
        achou ninguém — cria um cadastro novo como visitante, mesmo padrão
        de `PublicVisitorForm.save()`. Só sobrescreve o que a resposta
        realmente preencheu — nunca apaga um dado existente com um campo
        deixado em branco."""
        data = {
            attr: cleaned[field.pk]
            for field in fields
            if (attr := PERSON_FIELD_MAP.get(field.field_type)) and cleaned.get(field.pk)
        }
        if not data:
            return existing_person

        person = existing_person
        if person is None:
            phone_digits = "".join(ch for ch in data.get("phone", "") if ch.isdigit())
            if phone_digits:
                for candidate in Person.objects.exclude(phone=""):
                    if "".join(ch for ch in candidate.phone if ch.isdigit()) == phone_digits:
                        person = candidate
                        break
        if person is None and data.get("full_name"):
            person = Person.objects.filter(full_name__iexact=data["full_name"].strip()).first()
        if person is None:
            person = Person(
                church=church, is_visitor=True, status=Person.Status.VISITOR_ONLY, role=Person.Role.VISITOR
            )

        for attr, value in data.items():
            setattr(person, attr, value)
        person.save()
        return person

    @staticmethod
    def _notify_staff(custom_form, fields, cleaned):
        """Avisa a equipe por e-mail que uma resposta nova chegou — nunca
        derruba a submissão se o envio falhar (SMTP fora do ar, endereço
        inválido etc.), só loga."""
        lines = [
            f"{field.label}: {cleaned.get(field.pk) or '—'}"
            for field in fields if field.field_type != FormField.FieldType.FILE
        ]
        recipients = [email.strip() for email in custom_form.notify_staff_emails.split(",") if email.strip()]
        if not recipients:
            return
        try:
            send_mail(
                subject=f'Nova resposta em "{custom_form.title}"',
                message="Uma nova resposta foi recebida:\n\n" + "\n".join(lines),
                from_email=None,
                recipient_list=recipients,
                fail_silently=False,
            )
        except Exception:
            logger.exception('Falha ao notificar a equipe sobre nova resposta em "%s"', custom_form.title)

    @staticmethod
    def _queue_confirmation(custom_form, cleaned, church):
        """Enfileira (nunca envia na hora — mesma fila/intervalo de sempre,
        ver `notifications.WhatsAppMessage`) uma confirmação pra quem
        respondeu, só se um campo estiver marcado como telefone e a
        resposta tiver algum dígito nele."""
        phone_field = custom_form.phone_field
        if not phone_field:
            return
        digits = "".join(ch for ch in cleaned.get(phone_field.pk, "") if ch.isdigit())
        if not digits:
            return
        if not digits.startswith("55"):
            digits = "55" + digits

        name_field = custom_form.name_field
        nome = cleaned.get(name_field.pk, "").strip() if name_field else ""
        try:
            message = custom_form.whatsapp_message_template.format(nome=nome or "você", formulario=custom_form.title)
        except (KeyError, IndexError):
            message = f'Obrigado! Recebemos sua resposta para "{custom_form.title}".'

        WhatsAppMessage.objects.create(
            church=church, phone=digits, message=message, campaign_label=f"Formulário: {custom_form.title}",
        )


class PublicFormDoneView(PublicChurchMixin, TemplateView):
    template_name = "custom_forms/public_form_done.html"
