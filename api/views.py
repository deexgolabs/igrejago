"""API de leitura — sem Django REST Framework (não instalado no
projeto; pra só leitura simples, `JsonResponse` na mão segue o mesmo
espírito já usado aqui pra CSV/Excel com `openpyxl` direto em vez de
uma lib pronta). Cada view monta um `dict` explícito por objeto — campos
allowlisted à mão, nunca `model_to_dict()`/serializer genérico, pra
nunca vazar um campo sensível por esquecimento quando o model ganhar
um campo novo no futuro."""

import json

from django.forms.models import model_to_dict
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.generic import View

from api.auth import ApiKeyRateLimitMixin, ApiKeyRequiredMixin, paginate
from core.billing import pode_adicionar_pessoa
from events.models import Event, Registration
from finance.models import Transaction
from people.forms import PersonForm
from people.models import Person


class BaseApiView(ApiKeyRequiredMixin, ApiKeyRateLimitMixin, View):
    def get(self, request):
        data = paginate(request, self.get_queryset(), self.serialize)
        return JsonResponse(data)


def _parse_json_body(request):
    """`None` = corpo ausente/malformado/não é um objeto JSON — o
    chamador devolve 400 nesse caso. Um dict comum já basta pra
    alimentar um `ModelForm` (`data=payload`) — Django não exige
    `QueryDict`, só algo com `.get()`/`in`/(`.getlist()` opcional pra
    `SelectMultiple`, com fallback automático pra `.get()` quando
    ausente — é assim que `tags` funciona vindo de uma lista JSON)."""
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _form_errors(form):
    return {field: [str(error) for error in errors] for field, errors in form.errors.items()}


class PersonListCreateAPIView(BaseApiView):
    """`GET` lista (paginado, como antes); `POST` cria — reaproveita
    `people.PersonForm` tal como está: ele já reescopa os querysets de
    `department`/`family`/`tags`/`guardian` sozinho dentro do
    `tenant_context` que `ApiKeyRequiredMixin.dispatch` já ativa, então
    a validação aqui é EXATAMENTE a mesma da tela de cadastro normal —
    zero reimplementação."""

    def get_queryset(self):
        return Person.objects.order_by("id")

    @staticmethod
    def serialize(person):
        return {
            "id": person.pk, "full_name": person.full_name, "phone": person.phone, "email": person.email,
            "is_member": person.is_member, "is_visitor": person.is_visitor,
            "department": person.department.name if person.department_id else None,
        }

    def post(self, request):
        payload = _parse_json_body(request)
        if payload is None:
            return JsonResponse({"detail": "Corpo precisa ser um objeto JSON válido."}, status=400)
        if not pode_adicionar_pessoa(request.church):
            return JsonResponse(
                {"detail": "Seu plano atingiu o limite de pessoas cadastradas."}, status=403
            )

        form = PersonForm(data=payload)
        if not form.is_valid():
            return JsonResponse({"detail": "Dados inválidos.", "errors": _form_errors(form)}, status=400)

        person = form.save(commit=False)
        person.church = request.church
        person.save()
        form.save_m2m()
        return JsonResponse(self.serialize(person), status=201)


class PersonDetailAPIView(ApiKeyRequiredMixin, ApiKeyRateLimitMixin, View):
    """`GET`/`PATCH` em `/api/pessoas/<pk>/` — `PATCH` é parcial: parte
    dos valores ATUAIS da pessoa (`model_to_dict`, o mesmo utilitário
    que o Django usa internamente pra popular um form a partir de uma
    instância) e sobrepõe só os campos que vieram no JSON, antes de
    validar com o mesmo `PersonForm` — assim quem manda só `{"phone":
    "..."}` não precisa reenviar a pessoa inteira."""

    def get(self, request, pk):
        person = get_object_or_404(Person, pk=pk)
        return JsonResponse(PersonListCreateAPIView.serialize(person))

    def patch(self, request, pk):
        person = get_object_or_404(Person, pk=pk)
        payload = _parse_json_body(request)
        if payload is None:
            return JsonResponse({"detail": "Corpo precisa ser um objeto JSON válido."}, status=400)

        data = model_to_dict(person, fields=PersonForm.Meta.fields)
        data.update(payload)
        form = PersonForm(data=data, instance=person)
        if not form.is_valid():
            return JsonResponse({"detail": "Dados inválidos.", "errors": _form_errors(form)}, status=400)

        form.save()
        return JsonResponse(PersonListCreateAPIView.serialize(person))


class TransactionListAPIView(BaseApiView):
    """Só entradas (doações/dízimo/oferta) — não expõe despesas/saída
    pela API por padrão (informação financeira mais sensível, sem um
    caso de uso claro pra terceiro ler via integração)."""

    def get_queryset(self):
        return Transaction.objects.filter(type=Transaction.Type.INCOME).order_by("-date", "-id")

    @staticmethod
    def serialize(transaction):
        return {
            "id": transaction.pk, "category": transaction.category, "amount": str(transaction.amount),
            "date": transaction.date.isoformat(),
            "person_name": transaction.person.full_name if transaction.person_id else None,
        }


class EventListAPIView(BaseApiView):
    def get_queryset(self):
        return Event.objects.order_by("-start_datetime")

    @staticmethod
    def serialize(event):
        return {
            "id": event.pk, "title": event.title, "status": event.status,
            "start_datetime": event.start_datetime.isoformat(),
            "is_paid": event.is_paid, "price": str(event.price),
        }


class RegistrationListAPIView(BaseApiView):
    def get_queryset(self):
        return Registration.objects.select_related("event").order_by("-id")

    @staticmethod
    def serialize(registration):
        return {
            "id": registration.pk, "event": registration.event.title, "full_name": registration.full_name,
            "payment_status": registration.payment_status, "on_waitlist": registration.on_waitlist,
        }
