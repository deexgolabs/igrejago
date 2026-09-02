"""API de leitura — sem Django REST Framework (não instalado no
projeto; pra só leitura simples, `JsonResponse` na mão segue o mesmo
espírito já usado aqui pra CSV/Excel com `openpyxl` direto em vez de
uma lib pronta). Cada view monta um `dict` explícito por objeto — campos
allowlisted à mão, nunca `model_to_dict()`/serializer genérico, pra
nunca vazar um campo sensível por esquecimento quando o model ganhar
um campo novo no futuro."""

from django.http import JsonResponse
from django.views.generic import View

from api.auth import ApiKeyRateLimitMixin, ApiKeyRequiredMixin, paginate
from events.models import Event, Registration
from finance.models import Transaction
from people.models import Person


class BaseApiView(ApiKeyRequiredMixin, ApiKeyRateLimitMixin, View):
    def get(self, request):
        data = paginate(request, self.get_queryset(), self.serialize)
        return JsonResponse(data)


class PersonListAPIView(BaseApiView):
    def get_queryset(self):
        return Person.objects.order_by("id")

    @staticmethod
    def serialize(person):
        return {
            "id": person.pk, "full_name": person.full_name, "phone": person.phone, "email": person.email,
            "is_member": person.is_member, "is_visitor": person.is_visitor,
            "department": person.department.name if person.department_id else None,
        }


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
