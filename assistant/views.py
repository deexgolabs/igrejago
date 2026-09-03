from datetime import datetime

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import ListView, View

from accounts.mixins import IsChurchManagerMixin
from assistant import alerts
from assistant.forms import PersonUpdateForm
from assistant.models import PERSON_DRAFT_ALLOWED_FIELDS, Conversation, PersonDraft, PersonUpdateLink
from people.models import Person


class PersonDraftListView(IsChurchManagerMixin, ListView):
    """Fila de cadastros/atualizações pendentes — origem WhatsApp (IA) ou
    formulário público de atualização, mesma fila pras duas."""

    model = PersonDraft
    template_name = "assistant/persondraft_list.html"
    context_object_name = "drafts"

    def get_queryset(self):
        return PersonDraft.objects.filter(status=PersonDraft.Status.PENDING).select_related("person")


class PersonDraftApproveView(IsChurchManagerMixin, View):
    """Materializa o rascunho num `Person` de verdade — cria se
    `draft.person` estiver vazio (visitante novo), atualiza campo a
    campo se já existir. Só toca em campos da allow-list, mesmo que
    `data` tenha (não deveria) alguma outra chave."""

    def post(self, request, pk):
        draft = get_object_or_404(PersonDraft, pk=pk, status=PersonDraft.Status.PENDING)
        person = draft.person
        if person is None:
            person = Person(
                church=request.church, is_visitor=True,
                status=Person.Status.VISITOR_ONLY, role=Person.Role.VISITOR,
            )
        _aplicar_dados_ao_person(person, draft.data)
        person.save()

        draft.person = person
        draft.status = PersonDraft.Status.APPROVED
        draft.processed_by = request.user
        draft.processed_at = timezone.now()
        draft.save(update_fields=["person", "status", "processed_by", "processed_at"])
        messages.success(request, f"Cadastro de {person.full_name} confirmado.")
        return redirect("assistant:draft_list")


class PersonDraftRejectView(IsChurchManagerMixin, View):
    def post(self, request, pk):
        draft = get_object_or_404(PersonDraft, pk=pk, status=PersonDraft.Status.PENDING)
        draft.status = PersonDraft.Status.REJECTED
        draft.rejection_reason = request.POST.get("rejection_reason", "").strip()
        draft.processed_by = request.user
        draft.processed_at = timezone.now()
        draft.save(update_fields=["status", "rejection_reason", "processed_by", "processed_at"])
        messages.success(request, "Rascunho rejeitado.")
        return redirect("assistant:draft_list")


class ConversationHumanQueueListView(IsChurchManagerMixin, ListView):
    """Conversas que escolheram "2 — falar com a secretaria" — o bot
    parou de responder sozinho, alguém precisa continuar por fora (tela
    de mensagem avulsa já existente, `notifications.ScheduledMessageCreateView`)."""

    model = Conversation
    template_name = "assistant/conversation_human_queue.html"
    context_object_name = "conversations"

    def get_queryset(self):
        return Conversation.objects.filter(state=Conversation.State.AGUARDANDO_HUMANO).select_related("person")


class PersonUpdateFormView(View):
    """Pública, sem login, resolvida por token — mesmo padrão de
    `escalas.ConfirmarEscalaView`. Nunca liga o form direto na `Person`
    real: todo POST vira um `PersonDraft` pendente, mesma fila do
    WhatsApp (consistência de revisão pra secretaria, e defesa contra
    link encaminhado/preenchido por engano)."""

    template_name = "assistant/person_update_form.html"

    def get(self, request, token):
        link = get_object_or_404(PersonUpdateLink.todas_as_igrejas.select_related("person"), token=token)
        initial = {campo: getattr(link.person, campo) for campo in PERSON_DRAFT_ALLOWED_FIELDS}
        form = PersonUpdateForm(initial=initial)
        return render(request, self.template_name, {"form": form, "person": link.person, "church": link.church})

    def post(self, request, token):
        link = get_object_or_404(PersonUpdateLink.todas_as_igrejas.select_related("person"), token=token)
        form = PersonUpdateForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "person": link.person, "church": link.church})

        dados = {}
        for campo in PERSON_DRAFT_ALLOWED_FIELDS:
            valor = form.cleaned_data.get(campo)
            if valor in (None, ""):
                continue
            dados[campo] = valor.isoformat() if campo == "birth_date" else str(valor)

        if dados:
            draft = PersonDraft.objects.create(
                church=link.church, person=link.person, origin=PersonDraft.Origin.PUBLIC_FORM, data=dados,
            )
            alerts.avisar_novo_draft(draft)
        link.last_used_at = timezone.now()
        link.save(update_fields=["last_used_at"])
        return render(request, "assistant/person_update_done.html", {"person": link.person})


def _aplicar_dados_ao_person(person, data):
    """`setattr` campo a campo, só da allow-list, com coerção de tipo
    pros dois campos que não são texto puro (`birth_date` vem como
    string ISO no JSON; `gender`/`marital_status` são validados contra
    os choices reais — um valor fora disso é descartado em vez de
    quebrar o save())."""
    for campo, valor in (data or {}).items():
        if campo not in PERSON_DRAFT_ALLOWED_FIELDS or not valor:
            continue
        if campo == "birth_date":
            try:
                valor = datetime.strptime(valor, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                continue
        elif campo == "gender" and valor not in dict(Person.Gender.choices):
            continue
        elif campo == "marital_status" and valor not in dict(Person.MaritalStatus.choices):
            continue
        setattr(person, campo, valor)
