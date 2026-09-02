import calendar
from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DeleteView, DetailView, View

from accounts.mixins import CanManagePeopleMixin
from escalas.forms import EscalaForm
from escalas.models import Escala, EscalaVoluntario
from notifications.models import WhatsAppMessage

MESES_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


def _escalas_escopadas(user):
    """`Escala.objects` (todas) pra Pastor/Secretaria; só as do(s)
    departamento(s) liderado(s) pra um Líder de Departamento escopado —
    reaproveitado nas 5 views abaixo pra não duplicar a regra."""
    if user.is_unrestricted_manager:
        return Escala.objects.all()
    return Escala.objects.filter(department__in=user.led_departments)


class EscalaCalendarioView(CanManagePeopleMixin, View):
    template_name = "escalas/escala_calendario.html"

    def get(self, request):
        today = date.today()
        year = int(request.GET.get("ano", today.year))
        month = int(request.GET.get("mes", today.month))
        cal = calendar.Calendar(firstweekday=6)  # domingo primeiro

        escalas_by_day = {}
        escalas_do_mes = _escalas_escopadas(request.user).filter(
            date__year=year, date__month=month
        ).select_related("department")
        for escala in escalas_do_mes:
            escalas_by_day.setdefault(escala.date, []).append(escala)

        # Monta a estrutura já pronta pro template (dia + escalas do dia
        # juntos) — o motor de template do Django não faz busca em dict por
        # uma chave que é variável (só por literal), então isso precisa
        # acontecer aqui, não com um filtro no template.
        weeks = [
            [
                {"date": day, "in_month": day.month == month, "is_today": day == today,
                 "escalas": escalas_by_day.get(day, [])}
                for day in week
            ]
            for week in cal.monthdatescalendar(year, month)
        ]

        prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
        next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)

        context = {
            "weeks": weeks,
            "year": year, "month": month, "month_name": MESES_PT[month],
            "prev_year": prev_year, "prev_month": prev_month,
            "next_year": next_year, "next_month": next_month,
        }
        return render(request, self.template_name, context)


class EscalaDetailView(CanManagePeopleMixin, DetailView):
    model = Escala
    template_name = "escalas/escala_detail.html"
    context_object_name = "escala"

    def get_queryset(self):
        return _escalas_escopadas(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["voluntarios"] = self.object.voluntarios.select_related("person").order_by("person__full_name")
        return context


class EscalaCreateView(CanManagePeopleMixin, View):
    template_name = "escalas/escala_form.html"

    def get(self, request):
        form = EscalaForm(user=request.user)
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = EscalaForm(request.POST, user=request.user)
        if form.is_valid():
            escala = form.save(commit=False)
            escala.church = request.church
            escala.save()
            _sync_voluntarios(request, escala, form.cleaned_data["voluntarios"])
            messages.success(request, "Escala criada.")
            return redirect("escalas:detail", pk=escala.pk)
        return render(request, self.template_name, {"form": form})


class EscalaUpdateView(CanManagePeopleMixin, View):
    template_name = "escalas/escala_form.html"

    def get(self, request, pk):
        escala = get_object_or_404(_escalas_escopadas(request.user), pk=pk)
        form = EscalaForm(instance=escala, user=request.user)
        return render(request, self.template_name, {"form": form, "object": escala})

    def post(self, request, pk):
        escala = get_object_or_404(_escalas_escopadas(request.user), pk=pk)
        form = EscalaForm(request.POST, instance=escala, user=request.user)
        if form.is_valid():
            escala = form.save()
            _sync_voluntarios(request, escala, form.cleaned_data["voluntarios"])
            messages.success(request, "Escala atualizada.")
            return redirect("escalas:detail", pk=escala.pk)
        return render(request, self.template_name, {"form": form, "object": escala})


class EscalaDeleteView(CanManagePeopleMixin, DeleteView):
    model = Escala
    template_name = "escalas/escala_confirm_delete.html"

    def get_queryset(self):
        return _escalas_escopadas(self.request.user)

    def get_success_url(self):
        messages.success(self.request, "Escala removida.")
        return reverse("escalas:calendario")


def _sync_voluntarios(request, escala, pessoas):
    """Cria `EscalaVoluntario` pra quem foi adicionado agora (avisando por
    WhatsApp — nunca chama `enviar_whatsapp()` direto, sempre enfileira,
    mesmo padrão do resto do projeto) e remove quem foi desmarcado. Quem já
    estava e continua marcado não é tocado (não reenvia aviso à toa)."""
    existentes = {ev.person_id: ev for ev in escala.voluntarios.all()}
    selecionados_ids = {p.pk for p in pessoas}

    novas_mensagens = []
    for pessoa in pessoas:
        if pessoa.pk in existentes:
            continue
        ev = EscalaVoluntario.objects.create(church=request.church, escala=escala, person=pessoa)
        url = request.build_absolute_uri(reverse("escalas:confirmar", args=[ev.confirm_token]))
        horario = f" às {escala.time.strftime('%H:%M')}" if escala.time else ""
        texto = (
            f"Você foi escalado(a) para {escala.department.name} — {escala.date:%d/%m}{horario}. "
            f"Confirma presença? {url}"
        )
        novas_mensagens.append(WhatsAppMessage(
            church=request.church, person=pessoa, phone=pessoa.phone, message=texto,
            campaign_label=f"Escala-{escala.pk}",
        ))

    for pessoa_id, ev in existentes.items():
        if pessoa_id not in selecionados_ids:
            ev.delete()

    if novas_mensagens:
        WhatsAppMessage.objects.bulk_create(novas_mensagens)


class ConfirmarEscalaView(View):
    """Link público (por token, sem login) que o voluntário recebe por
    WhatsApp — mesmo espírito de `core.ConfirmEmailView`."""

    template_name = "escalas/confirmar.html"

    def get(self, request, token):
        voluntario = get_object_or_404(EscalaVoluntario.todas_as_igrejas, confirm_token=token)
        return render(request, self.template_name, {"voluntario": voluntario})

    def post(self, request, token):
        voluntario = get_object_or_404(EscalaVoluntario.todas_as_igrejas, confirm_token=token)
        acao = request.POST.get("acao")
        if acao == "confirmar":
            voluntario.status = EscalaVoluntario.Status.CONFIRMED
        elif acao == "recusar":
            voluntario.status = EscalaVoluntario.Status.DECLINED
        voluntario.confirmed_at = timezone.now()
        voluntario.save(update_fields=["status", "confirmed_at"])
        return render(request, self.template_name, {"voluntario": voluntario, "respondido": True})
