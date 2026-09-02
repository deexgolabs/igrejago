import calendar
from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DeleteView, DetailView, ListView, View

from accounts.mixins import CanManagePeopleMixin
from escalas.forms import EscalaForm, IndisponibilidadeForm
from escalas.models import Escala, EscalaVoluntario, IndisponibilidadeVoluntario, TrocaEscala
from notifications.models import WhatsAppMessage
from people.models import Person

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
        voluntarios = list(self.object.voluntarios.select_related("person").order_by("person__full_name"))
        # Anotado aqui (não no template — Django template não faz
        # `.filter()` com argumento) pra dar visibilidade de troca
        # pendente/aceita sem a secretaria precisar entrar em cada troca.
        for v in voluntarios:
            v.troca_ativa = v.trocas.exclude(status=TrocaEscala.Status.CANCELADA).order_by("-created_at").first()
        context["voluntarios"] = voluntarios
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
            church=request.church, person=pessoa, phone=pessoa.whatsapp_number, message=texto,
            campaign_label=f"Escala-{escala.pk}",
        ))

    for pessoa_id, ev in existentes.items():
        if pessoa_id not in selecionados_ids:
            ev.delete()

    if novas_mensagens:
        WhatsAppMessage.objects.bulk_create(novas_mensagens)


class ConfirmarEscalaView(View):
    """Link público (por token, sem login) que o voluntário recebe por
    WhatsApp — mesmo espírito de `core.ConfirmEmailView`. Depois de
    confirmado, mostra também o botão "Pedir troca" (`PedirTrocaView`) —
    self-service, sem precisar da secretaria."""

    template_name = "escalas/confirmar.html"

    def get(self, request, token):
        voluntario = get_object_or_404(EscalaVoluntario.todas_as_igrejas, confirm_token=token)
        return render(request, self.template_name, self._context(voluntario))

    def post(self, request, token):
        voluntario = get_object_or_404(EscalaVoluntario.todas_as_igrejas, confirm_token=token)
        acao = request.POST.get("acao")
        if acao == "confirmar":
            voluntario.status = EscalaVoluntario.Status.CONFIRMED
        elif acao == "recusar":
            voluntario.status = EscalaVoluntario.Status.DECLINED
        voluntario.confirmed_at = timezone.now()
        voluntario.save(update_fields=["status", "confirmed_at"])
        return render(request, self.template_name, self._context(voluntario, respondido=True))

    @staticmethod
    def _context(voluntario, respondido=False):
        troca_pendente = voluntario.trocas.filter(status=TrocaEscala.Status.PENDING).exists()
        return {"voluntario": voluntario, "respondido": respondido, "troca_pendente": troca_pendente}


class PedirTrocaView(View):
    """Pede pra repassar a própria escala — POST-only, pelo mesmo token
    público de `ConfirmarEscalaView` (não precisa de login: quem recebeu
    o WhatsApp original é quem tem o link). Só faz sentido pra quem já
    confirmou presença (`CONFIRMED`) e ainda não tem troca pendente."""

    def post(self, request, token):
        voluntario = get_object_or_404(EscalaVoluntario.todas_as_igrejas, confirm_token=token)
        if voluntario.status != EscalaVoluntario.Status.CONFIRMED:
            messages.error(request, "Só dá pra pedir troca de uma escala já confirmada.")
            return redirect("escalas:confirmar", token=token)
        if voluntario.trocas.filter(status=TrocaEscala.Status.PENDING).exists():
            messages.error(request, "Você já pediu troca dessa escala — aguarde alguém aceitar.")
            return redirect("escalas:confirmar", token=token)

        troca = TrocaEscala.objects.create(church=voluntario.church, escala_voluntario=voluntario)
        escala = voluntario.escala
        colegas = Person.objects.filter(department=escala.department).exclude(pk=voluntario.person_id)
        colegas = [p for p in colegas if p.phone]

        if colegas:
            url = request.build_absolute_uri(reverse("escalas:aceitar_troca", args=[troca.token]))
            horario = f" às {escala.time.strftime('%H:%M')}" if escala.time else ""
            texto = (
                f"{voluntario.person.full_name} não vai poder servir em {escala.department.name} — "
                f"{escala.date:%d/%m}{horario} e está passando a vez. Pode ir no lugar? {url}"
            )
            WhatsAppMessage.objects.bulk_create([
                WhatsAppMessage(
                    church=voluntario.church, person=colega, phone=colega.whatsapp_number, message=texto,
                    campaign_label=f"Troca de escala-{troca.pk}",
                )
                for colega in colegas
            ])
            messages.success(request, "Pedido de troca enviado pros colegas do departamento.")
        else:
            messages.warning(request, "Pedido de troca criado, mas não há outro voluntário com telefone no departamento pra avisar.")
        return redirect("escalas:confirmar", token=token)


class AceitarTrocaView(View):
    """Link público (por token) que os colegas recebem — o PRIMEIRO que
    aceitar assume a escala. `select_for_update` dentro de uma transação
    evita 2 pessoas aceitando ao mesmo tempo (condição de corrida real:
    2 WhatsApp chegam juntos, 2 cliques quase simultâneos)."""

    template_name = "escalas/aceitar_troca.html"

    def get(self, request, token):
        troca = get_object_or_404(TrocaEscala.todas_as_igrejas, token=token)
        context = {"troca": troca}
        if troca.status == TrocaEscala.Status.PENDING:
            voluntario = troca.escala_voluntario
            context["colegas"] = Person.todas_as_igrejas.filter(
                church=troca.church, department=voluntario.escala.department,
            ).exclude(pk=voluntario.person_id).order_by("full_name")
        return render(request, self.template_name, context)

    def post(self, request, token):
        with transaction.atomic():
            troca = get_object_or_404(TrocaEscala.todas_as_igrejas.select_for_update(), token=token)
            if troca.status != TrocaEscala.Status.PENDING:
                return render(request, self.template_name, {"troca": troca, "ja_resolvida": True})

            voluntario = troca.escala_voluntario
            aceitante_id = request.POST.get("person_id")
            aceitante = get_object_or_404(
                Person.todas_as_igrejas.filter(church=troca.church, department=voluntario.escala.department)
                .exclude(pk=voluntario.person_id),
                pk=aceitante_id,
            )

            voluntario.person = aceitante
            voluntario.status = EscalaVoluntario.Status.PENDING
            voluntario.confirmed_at = None
            voluntario.save(update_fields=["person", "status", "confirmed_at"])

            troca.status = TrocaEscala.Status.ACEITA
            troca.aceito_por = aceitante
            troca.resolved_at = timezone.now()
            troca.save(update_fields=["status", "aceito_por", "resolved_at"])

        confirm_url = request.build_absolute_uri(reverse("escalas:confirmar", args=[voluntario.confirm_token]))
        escala = voluntario.escala
        horario = f" às {escala.time.strftime('%H:%M')}" if escala.time else ""
        WhatsAppMessage.objects.create(
            church=troca.church, person=aceitante, phone=aceitante.whatsapp_number,
            message=(
                f"Você assumiu a escala de {escala.department.name} — {escala.date:%d/%m}{horario}. "
                f"Confirma presença? {confirm_url}"
            ),
            campaign_label=f"Troca de escala-{troca.pk}",
        )
        return render(request, self.template_name, {"troca": troca, "aceita_agora": True})


class MinhaIndisponibilidadeListView(LoginRequiredMixin, ListView):
    """Autosserviço do Portal — a pessoa avisa datas em que não pode
    servir, pro rodízio de `gerar_escalas_mensais` pular ela nesses dias.
    Sem mixin de gestão: qualquer pessoa com login e `person` vinculado
    pode usar, é sobre a própria disponibilidade."""

    template_name = "escalas/minha_indisponibilidade.html"
    context_object_name = "indisponibilidades"

    def get_queryset(self):
        if not self.request.user.person_id:
            return IndisponibilidadeVoluntario.objects.none()
        return IndisponibilidadeVoluntario.objects.filter(
            person=self.request.user.person, date__gte=date.today()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = IndisponibilidadeForm()
        return context

    def post(self, request):
        if not request.user.person_id:
            messages.error(request, "Seu login ainda não está vinculado a um cadastro de pessoa.")
            return redirect("escalas:minha_indisponibilidade")
        form = IndisponibilidadeForm(request.POST)
        if form.is_valid():
            indisponibilidade = form.save(commit=False)
            indisponibilidade.church = request.church
            indisponibilidade.person = request.user.person
            try:
                # `transaction.atomic()` aqui é o que faz o `except`
                # funcionar de verdade: sem o savepoint próprio, um
                # `IntegrityError` "envenena" a transação inteira da
                # request (herda de fora, ex.: `ATOMIC_REQUESTS`/testes
                # que envolvem cada teste numa transação) e qualquer
                # query seguinte (até o `redirect`) quebraria com
                # `TransactionManagementError` em vez do erro amigável.
                with transaction.atomic():
                    indisponibilidade.save()
                messages.success(request, "Indisponibilidade registrada.")
            except IntegrityError:
                messages.error(request, "Você já marcou essa data como indisponível.")
            return redirect("escalas:minha_indisponibilidade")
        return render(request, self.template_name, {"form": form, "indisponibilidades": self.get_queryset()})


class MinhaIndisponibilidadeDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        indisponibilidade = get_object_or_404(
            IndisponibilidadeVoluntario, pk=pk, person=request.user.person
        )
        indisponibilidade.delete()
        messages.success(request, "Indisponibilidade removida.")
        return redirect("escalas:minha_indisponibilidade")
