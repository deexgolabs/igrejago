import csv
import json
import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, Sum
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from accounts.mixins import IsChurchManagerMixin
from core.models import Church
from core.qr import qr_data_uri
from core.tenancy import TenantFormMixin
from core.reports import (
    generate_annual_donation_receipt_pdf,
    generate_balancete_pdf,
    generate_donation_receipt_pdf,
    generate_dre_pdf,
)
from events.pix import build_pix_payload
from finance import mercadopago
from finance.dre import dre_breakdown, saldo_acumulado
from finance.forms import DonationAmountForm, RecurringPledgeForm, RecurringPledgeSubscribeForm, TransactionForm
from finance.models import Budget, Donation, RecurringPledge, Transaction
from people.models import Person

logger = logging.getLogger(__name__)


def _periodo_do_get(request):
    """`?inicio=&fim=` (AAAA-MM-DD) — sem os dois, cai no mês atual
    inteiro. Usado por `DREView`/`BalanceteView`."""
    hoje = date.today()
    try:
        inicio = date.fromisoformat(request.GET["inicio"]) if request.GET.get("inicio") else hoje.replace(day=1)
    except ValueError:
        inicio = hoje.replace(day=1)
    try:
        fim = date.fromisoformat(request.GET["fim"]) if request.GET.get("fim") else hoje
    except ValueError:
        fim = hoje
    return inicio, fim


class TransactionListView(IsChurchManagerMixin, ListView):
    model = Transaction
    template_name = "finance/transaction_list.html"
    context_object_name = "transactions"
    paginate_by = 50

    def get_queryset(self):
        qs = Transaction.objects.select_related("person")

        type_filter = self.request.GET.get("type", "")
        if type_filter:
            qs = qs.filter(type=type_filter)

        category = self.request.GET.get("category", "")
        if category:
            qs = qs.filter(category=category)

        month = self.request.GET.get("month", "")
        if month:
            try:
                year, mon = (int(part) for part in month.split("-"))
                qs = qs.filter(date__year=year, date__month=mon)
            except ValueError:
                pass

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["type_choices"] = Transaction.Type.choices
        context["category_choices"] = Transaction.Category.choices
        context["current_filters"] = {
            "type": self.request.GET.get("type", ""),
            "category": self.request.GET.get("category", ""),
            "month": self.request.GET.get("month", date.today().strftime("%Y-%m")),
        }

        all_filtered = self.get_queryset()
        totals = all_filtered.aggregate(
            income=Sum("amount", filter=Q(type=Transaction.Type.INCOME)),
            expense=Sum("amount", filter=Q(type=Transaction.Type.EXPENSE)),
        )
        income = totals["income"] or 0
        expense = totals["expense"] or 0
        context["total_income"] = income
        context["total_expense"] = expense
        context["balance"] = income - expense
        context["monthly_chart"] = json.dumps(self._monthly_totals_last_6_months())
        return context

    @staticmethod
    def _monthly_totals_last_6_months():
        today = date.today()
        labels, income_totals, expense_totals = [], [], []
        for i in range(5, -1, -1):
            month_start = today.replace(day=1) - relativedelta(months=i)
            month_end = month_start + relativedelta(months=1)
            month_qs = Transaction.objects.filter(date__gte=month_start, date__lt=month_end)
            totals = month_qs.aggregate(
                income=Sum("amount", filter=Q(type=Transaction.Type.INCOME)),
                expense=Sum("amount", filter=Q(type=Transaction.Type.EXPENSE)),
            )
            labels.append(month_start.strftime("%b/%Y"))
            income_totals.append(float(totals["income"] or 0))
            expense_totals.append(float(totals["expense"] or 0))
        return {"labels": labels, "income": income_totals, "expense": expense_totals}


class TransactionExportView(IsChurchManagerMixin, View):
    def get(self, request):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="financeiro.csv"'
        response["Cache-Control"] = "private, no-store"
        response.write("﻿")

        writer = csv.writer(response)
        writer.writerow(["Data", "Tipo", "Categoria", "Descrição", "Contribuinte", "Forma de pagamento", "Valor"])
        for t in Transaction.objects.select_related("person").order_by("date"):
            writer.writerow([
                t.date.strftime("%d/%m/%Y"), t.get_type_display(), t.get_category_display(),
                t.description, t.person.full_name if t.person else "",
                t.get_payment_method_display() if t.payment_method else "", t.amount,
            ])
        return response


class TransactionExportExcelView(IsChurchManagerMixin, View):
    """Mesmo conteúdo do CSV, em .xlsx — segue o padrão de
    `people.PersonImportTemplateView` (openpyxl direto, sem lib extra)."""

    def get(self, request):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Financeiro"
        ws.append(["Data", "Tipo", "Categoria", "Descrição", "Contribuinte", "Forma de pagamento", "Valor"])
        for t in Transaction.objects.select_related("person").order_by("date"):
            ws.append([
                t.date.strftime("%d/%m/%Y"), t.get_type_display(), t.get_category_display(),
                t.description, t.person.full_name if t.person else "",
                t.get_payment_method_display() if t.payment_method else "", float(t.amount),
            ])
        for column_cells in ws.columns:
            width = max(len(str(cell.value)) for cell in column_cells) + 2
            ws.column_dimensions[column_cells[0].column_letter].width = width

        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = 'attachment; filename="financeiro.xlsx"'
        response["Cache-Control"] = "private, no-store"
        wb.save(response)
        return response


class TransactionCreateView(TenantFormMixin, IsChurchManagerMixin, CreateView):
    model = Transaction
    form_class = TransactionForm
    template_name = "finance/transaction_form.html"
    success_url = reverse_lazy("finance:list")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, "Lançamento registrado.")
        return super().form_valid(form)


class TransactionUpdateView(IsChurchManagerMixin, UpdateView):
    model = Transaction
    form_class = TransactionForm
    template_name = "finance/transaction_form.html"
    success_url = reverse_lazy("finance:list")

    def form_valid(self, form):
        messages.success(self.request, "Lançamento atualizado.")
        return super().form_valid(form)


class TransactionDeleteView(IsChurchManagerMixin, DeleteView):
    model = Transaction
    template_name = "finance/transaction_confirm_delete.html"
    success_url = reverse_lazy("finance:list")

    def form_valid(self, form):
        messages.success(self.request, "Lançamento removido.")
        return super().form_valid(form)


class BudgetView(IsChurchManagerMixin, View):
    """Meta prevista x realizado por categoria, mês a mês — não um form
    Django/CBV comum porque a "linha" é uma categoria fixa (do
    `TextChoices`), não um objeto que já existe no banco; monta a tabela
    na mão a partir de `Transaction.Category.choices` + o `Budget` (se
    existir) daquele mês."""

    template_name = "finance/budget.html"

    def get(self, request):
        year, month = self._parse_month(request.GET.get("month"))
        return render(request, self.template_name, self._build_context(year, month))

    def post(self, request):
        year, month = self._parse_month(request.POST.get("month"))
        for category, _ in Transaction.Category.choices:
            raw_value = request.POST.get(f"target_{category}", "").strip()
            if not raw_value:
                continue
            try:
                target = Decimal(raw_value.replace(",", "."))
            except InvalidOperation:
                continue
            Budget.objects.update_or_create(
                church=request.church, category=category, year=year, month=month,
                defaults={"target_amount": target},
            )
        messages.success(request, "Orçamento salvo.")
        return render(request, self.template_name, self._build_context(year, month))

    @staticmethod
    def _parse_month(raw):
        today = date.today()
        if raw:
            try:
                year, month = (int(part) for part in raw.split("-"))
                return year, month
            except ValueError:
                pass
        return today.year, today.month

    @staticmethod
    def _build_context(year, month):
        budgets = {b.category: b.target_amount for b in Budget.objects.filter(year=year, month=month)}
        actuals = {
            row["category"]: row["total"]
            for row in Transaction.objects.filter(date__year=year, date__month=month)
                .values("category").annotate(total=Sum("amount"))
        }
        rows = []
        for category, label in Transaction.Category.choices:
            target = budgets.get(category, 0)
            actual = actuals.get(category, 0)
            rows.append({
                "category": category, "label": label, "target": target, "actual": actual,
                "diff": actual - target,
            })
        return {"rows": rows, "month_value": f"{year:04d}-{month:02d}"}


class RecurringPledgeListView(IsChurchManagerMixin, View):
    """Lista as contribuições recorrentes ativas e cruza cada uma com o
    mês corrente: "em dia" se já existe um `Transaction` (categoria
    Dízimo) daquela pessoa neste mês, "em atraso" caso contrário. Não
    tenta achar um valor exato batendo — só se a pessoa contribuiu ou não
    no mês, que é o que importa pra secretaria fazer o acompanhamento."""

    template_name = "finance/recurring_pledge_list.html"

    def get(self, request):
        return render(request, self.template_name, {
            "form": RecurringPledgeForm(), "rows": self._build_rows(),
        })

    def post(self, request):
        form = RecurringPledgeForm(request.POST)
        if form.is_valid():
            pledge = form.save(commit=False)
            pledge.church = request.church
            pledge.save()
            messages.success(request, "Contribuição recorrente cadastrada.")
            return redirect("finance:recurring_pledges")
        return render(request, self.template_name, {"form": form, "rows": self._build_rows()})

    @staticmethod
    def _build_rows():
        today = date.today()
        paid_person_ids = set(
            Transaction.objects.filter(
                category=Transaction.Category.TITHE,
                date__year=today.year, date__month=today.month,
                person__isnull=False,
            ).values_list("person_id", flat=True)
        )
        rows = []
        for pledge in RecurringPledge.objects.filter(active=True).select_related("person"):
            rows.append({"pledge": pledge, "em_dia": pledge.person_id in paid_person_ids})
        return rows


class RecurringPledgeToggleView(IsChurchManagerMixin, View):
    def post(self, request, pk):
        pledge = get_object_or_404(RecurringPledge, pk=pk)
        pledge.active = not pledge.active
        pledge.save(update_fields=["active"])
        return redirect("finance:recurring_pledges")


class RecurringPledgeDeleteView(IsChurchManagerMixin, View):
    def post(self, request, pk):
        get_object_or_404(RecurringPledge, pk=pk).delete()
        messages.success(request, "Contribuição recorrente removida.")
        return redirect("finance:recurring_pledges")


class RecurringPledgeSubscribeView(LoginRequiredMixin, View):
    """Assinatura de dízimo mensal AUTOMÁTICA via Mercado Pago, iniciada
    pelo PRÓPRIO membro no Portal — diferente de `RecurringPledgeListView`
    (secretaria cadastrando um compromisso manual de qualquer pessoa).
    Mesmo esqueleto de `DonationMercadoPagoStartView` (preferência única):
    aqui é uma assinatura (Preapproval), API de Assinaturas."""

    template_name = "finance/recurring_pledge_subscribe.html"

    def get(self, request):
        return render(request, self.template_name, {
            "form": RecurringPledgeSubscribeForm(), "pledge": self._minha_assinatura(request),
        })

    def post(self, request):
        if not request.user.person_id:
            messages.error(request, "Seu login ainda não está vinculado a um cadastro de pessoa.")
            return redirect("core:dashboard")
        church_config = request.church
        if not church_config.mercadopago_configured:
            messages.error(request, "Pagamento via Mercado Pago não está configurado.")
            return redirect("core:dashboard")

        form = RecurringPledgeSubscribeForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "pledge": self._minha_assinatura(request)})

        person = request.user.person
        pledge = RecurringPledge.objects.create(
            church=church_config, person=person, active=False,
            monthly_amount=form.cleaned_data["monthly_amount"], due_day=form.cleaned_data["due_day"],
        )
        base_url = request.build_absolute_uri("/")[:-1]
        notification_url = (
            base_url + reverse("finance:recurring_pledge_webhook") + f"?church_id={church_config.pk}"
        )
        try:
            preapproval_id, init_point = mercadopago.criar_assinatura_dizimo(
                access_token=church_config.mercadopago_access_token,
                pledge=pledge,
                payer_email=person.email or request.user.email,
                back_url=base_url + reverse("core:dashboard"),
                notification_url=notification_url,
            )
        except Exception:
            logger.exception("Falha ao criar assinatura de dízimo no Mercado Pago (pledge %s)", pledge.pk)
            pledge.delete()
            messages.error(request, "Não foi possível iniciar a assinatura pelo Mercado Pago agora. Tente de novo em instantes.")
            return redirect("finance:recurring_pledge_subscribe")
        pledge.mercadopago_preapproval_id = preapproval_id
        pledge.save(update_fields=["mercadopago_preapproval_id"])
        return redirect(init_point)

    @staticmethod
    def _minha_assinatura(request):
        if not request.user.person_id:
            return None
        return RecurringPledge.objects.filter(
            person=request.user.person
        ).exclude(mercadopago_preapproval_id="").order_by("-created_at").first()


class RecurringPledgeCancelView(LoginRequiredMixin, View):
    """Cancela uma assinatura de dízimo — o próprio dono (`person ==
    request.user.person`) ou quem gerencia o financeiro. Diferente de
    `RecurringPledgeDeleteView` (secretaria removendo um compromisso
    manual): essa aqui de fato desliga a cobrança no Mercado Pago antes
    de desativar localmente."""

    def post(self, request, pk):
        pledge = get_object_or_404(RecurringPledge, pk=pk)
        is_owner = pledge.person_id and request.user.person_id == pledge.person_id
        if not (is_owner or request.user.is_unrestricted_manager):
            raise Http404
        if pledge.mercadopago_preapproval_id:
            try:
                mercadopago.cancelar_assinatura(
                    access_token=pledge.church.mercadopago_access_token,
                    preapproval_id=pledge.mercadopago_preapproval_id,
                )
                pledge.mercadopago_status = "cancelled"
                pledge.save(update_fields=["mercadopago_status"])
            except Exception:
                logger.exception("Falha ao cancelar assinatura no Mercado Pago (pledge %s)", pledge.pk)
                messages.error(request, "Não foi possível cancelar no Mercado Pago agora. Tente de novo em instantes.")
                return redirect("finance:recurring_pledges" if request.user.is_unrestricted_manager else "core:dashboard")
        pledge.active = False
        pledge.save(update_fields=["active"])
        messages.success(request, "Assinatura cancelada.")
        return redirect("finance:recurring_pledges" if request.user.is_unrestricted_manager else "core:dashboard")


@method_decorator(csrf_exempt, name="dispatch")
class RecurringPledgeMercadoPagoWebhookView(View):
    """Mesmo padrão de `DonationWebhookView` — igreja vem do `?church_id=`
    embutido na notification_url, nunca confia no corpo do POST, sempre
    reconsulta. O Mercado Pago manda 2 tipos de notificação pra
    assinatura: `subscription_preapproval` (mudança de status da
    assinatura em si — autorizada/pausada/cancelada) e
    `subscription_authorized_payment` (uma cobrança mensal específica —
    é aqui que nasce o `Transaction` de cada mês)."""

    def post(self, request):
        topic = request.GET.get("type") or request.GET.get("topic")
        object_id = request.GET.get("data.id") or request.GET.get("id")
        church_id = request.GET.get("church_id")
        if not object_id or not church_id:
            return HttpResponseBadRequest("missing id or church_id")

        church_config = get_object_or_404(Church, pk=church_id)
        if not church_config.mercadopago_configured:
            return HttpResponseBadRequest("mercadopago not configured")

        if topic == "subscription_preapproval":
            self._processar_preapproval(church_config, object_id)
        elif topic == "subscription_authorized_payment":
            self._processar_pagamento_autorizado(church_config, object_id)
        return HttpResponse(status=200)

    @staticmethod
    def _processar_preapproval(church_config, preapproval_id):
        try:
            data = mercadopago.consultar_assinatura(
                access_token=church_config.mercadopago_access_token, preapproval_id=preapproval_id,
            )
        except Exception:
            logger.exception("Falha ao reconsultar assinatura %s no Mercado Pago", preapproval_id)
            return
        pledge = RecurringPledge.todas_as_igrejas.filter(
            mercadopago_preapproval_id=preapproval_id, church=church_config,
        ).first()
        if not pledge:
            return
        status = data.get("status", "")
        pledge.mercadopago_status = status
        pledge.active = status == "authorized"
        pledge.save(update_fields=["mercadopago_status", "active"])

    @staticmethod
    def _processar_pagamento_autorizado(church_config, payment_id):
        try:
            data = mercadopago.consultar_pagamento_autorizado(
                access_token=church_config.mercadopago_access_token, payment_id=payment_id,
            )
        except Exception:
            logger.exception("Falha ao reconsultar pagamento autorizado %s no Mercado Pago", payment_id)
            return
        if data.get("status") != "approved":
            return
        preapproval_id = data.get("preapproval_id", "")
        pledge = RecurringPledge.todas_as_igrejas.filter(
            mercadopago_preapproval_id=preapproval_id, church=church_config,
        ).first()
        if not pledge:
            return
        Transaction.todas_as_igrejas.create(
            church=church_config,
            type=Transaction.Type.INCOME, category=Transaction.Category.TITHE,
            amount=data.get("transaction_amount") or pledge.monthly_amount,
            date=date.today(), description="Dízimo automático via Mercado Pago",
            person=pledge.person, payment_method=Transaction.PaymentMethod.CARD,
        )


class AnnualDonationReceiptPDFView(LoginRequiredMixin, View):
    """Recibo ANUAL consolidado — o próprio contribuinte baixa o dele
    (`?ano=`, padrão o ano corrente); pra baixar de outra pessoa, só quem
    gerencia pessoas (mesmo critério de `DonationReceiptPDFView`)."""

    def get(self, request, pk=None):
        if pk:
            if not request.user.can_manage_people:
                raise Http404
            person = get_object_or_404(Person, pk=pk)
        else:
            if not request.user.person_id:
                raise Http404
            person = request.user.person

        try:
            year = int(request.GET.get("ano", date.today().year))
        except ValueError:
            year = date.today().year

        pdf_bytes = generate_annual_donation_receipt_pdf(request.church, person, year)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="recibo-anual-{year}-{person.pk}.pdf"'
        # Achado ao vivo em produção: a Cloudflare cacheou essa URL (mesmo
        # caminho pra QUALQUER pessoa logada) e serviu o PDF de um membro
        # pro próximo que abrisse o link — `Cache-Control` explícito é o
        # que garante que um proxy/CDN na frente nunca guarda uma resposta
        # personalizada como essa.
        response["Cache-Control"] = "private, no-store"
        return response


class DonationCreateView(LoginRequiredMixin, View):
    """Doação avulsa a partir do Portal do Membro — valor livre, sem
    depender de estar ligado a um evento. Via Mercado Pago o webhook
    confirma sozinho e já cria o `Transaction`; via PIX manual, fica
    pendente até a secretaria conferir e confirmar (`DonationListView`)."""

    template_name = "finance/donation_form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": DonationAmountForm()})

    def post(self, request):
        form = DonationAmountForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        person = request.user.person if request.user.person_id else None
        donation = Donation.objects.create(
            church=request.church, person=person, amount=form.cleaned_data["amount"]
        )
        return redirect("finance:donation_pay", pk=donation.pk)


class DonationPayView(LoginRequiredMixin, View):
    template_name = "finance/donation_pay.html"

    def get(self, request, pk):
        donation = get_object_or_404(Donation, pk=pk)
        church_config = request.church
        context = {"donation": donation, "church_config": church_config}
        if church_config.pix_configured:
            payload = build_pix_payload(
                key=church_config.pix_key,
                receiver_name=church_config.pix_receiver_name,
                receiver_city=church_config.pix_receiver_city,
                amount=donation.amount,
                txid=f"DOACAO{donation.pk}",
            )
            context["pix_payload"] = payload
            context["pix_qr_data_uri"] = qr_data_uri(payload)
        return render(request, self.template_name, context)


class DonationMercadoPagoStartView(LoginRequiredMixin, View):
    def get(self, request, pk):
        donation = get_object_or_404(Donation, pk=pk)
        church_config = request.church
        if not church_config.mercadopago_configured:
            messages.error(request, "Pagamento via Mercado Pago não está configurado.")
            return redirect("finance:donation_pay", pk=pk)

        base_url = request.build_absolute_uri("/")[:-1]
        # `church_id` vai na notification_url pelo mesmo motivo do webhook
        # de eventos (`events.views.MercadoPagoCheckoutStartView`): o
        # Mercado Pago chama o webhook sem usuário logado.
        notification_url = (
            base_url + reverse("finance:donation_webhook") + f"?church_id={church_config.pk}"
        )
        try:
            checkout_url = mercadopago.criar_preferencia_doacao(
                access_token=church_config.mercadopago_access_token,
                donation=donation,
                back_url_success=base_url + reverse("finance:donation_pay", args=[pk]),
                notification_url=notification_url,
            )
        except Exception:
            logger.exception("Falha ao criar preferência no Mercado Pago para a doação %s", pk)
            messages.error(request, "Não foi possível iniciar o pagamento pelo Mercado Pago agora. Tente o PIX abaixo.")
            return redirect("finance:donation_pay", pk=pk)
        return redirect(checkout_url)


@method_decorator(csrf_exempt, name="dispatch")
class DonationWebhookView(View):
    """Mesmo padrão de `events.MercadoPagoWebhookView`: a igreja vem do
    `?church_id=` embutido na notification_url (`DonationMercadoPagoStartView`),
    não de usuário logado nem slug — o webhook é anônimo. Usa
    `todas_as_igrejas` porque não há igreja no thread-local aqui. Nunca
    confia no corpo do POST, sempre reconsulta a API antes de marcar como
    pago."""

    def post(self, request):
        payment_id = request.GET.get("data.id") or request.GET.get("id")
        church_id = request.GET.get("church_id")
        if not payment_id or not church_id:
            return HttpResponseBadRequest("missing payment id or church_id")

        church_config = get_object_or_404(Church, pk=church_id)
        if not church_config.mercadopago_configured:
            return HttpResponseBadRequest("mercadopago not configured")

        try:
            payment = mercadopago.consultar_pagamento(
                access_token=church_config.mercadopago_access_token, payment_id=payment_id
            )
        except Exception:
            logger.exception("Falha ao reconsultar pagamento %s no Mercado Pago", payment_id)
            return HttpResponse(status=502)

        external_reference = payment.get("external_reference", "")
        if payment.get("status") == "approved" and external_reference.startswith("DOACAO-"):
            donation_id = external_reference.removeprefix("DOACAO-")
            donation = Donation.todas_as_igrejas.filter(
                pk=donation_id, church=church_config, status=Donation.Status.PENDING
            ).first()
            if donation:
                donation.status = Donation.Status.PAID
                donation.payment_reference = str(payment_id)
                donation.save(update_fields=["status", "payment_reference"])
                Transaction.todas_as_igrejas.create(
                    church=church_config,
                    type=Transaction.Type.INCOME, category=Transaction.Category.DONATION,
                    amount=payment.get("transaction_amount") or donation.amount,
                    date=date.today(), description="Doação via Mercado Pago (Portal do Membro)",
                    person=donation.person, payment_method=Transaction.PaymentMethod.CARD,
                )
        return HttpResponse(status=200)


class DonationListView(IsChurchManagerMixin, ListView):
    model = Donation
    template_name = "finance/donation_list.html"
    context_object_name = "donations"
    paginate_by = 50

    def get_queryset(self):
        return Donation.objects.select_related("person").order_by("-created_at")


class DonationConfirmPixView(IsChurchManagerMixin, View):
    """Confirmação manual de uma doação PIX — a secretaria confere o
    extrato do banco e confirma aqui, igual ao PIX de inscrição em evento."""

    def post(self, request, pk):
        donation = get_object_or_404(Donation, pk=pk, status=Donation.Status.PENDING)
        donation.status = Donation.Status.PAID
        donation.save(update_fields=["status"])
        Transaction.objects.create(
            church=donation.church,
            type=Transaction.Type.INCOME, category=Transaction.Category.DONATION,
            amount=donation.amount, date=date.today(),
            description="Doação via PIX (Portal do Membro)",
            person=donation.person, payment_method=Transaction.PaymentMethod.PIX,
            created_by=request.user,
        )
        messages.success(request, "Doação confirmada e lançada no financeiro.")
        return redirect("finance:donation_list")


class DonationReceiptPDFView(LoginRequiredMixin, View):
    """Recibo em PDF de uma doação já confirmada — acessível por quem
    doou (a própria pessoa) ou por quem gerencia o financeiro; qualquer
    outro logado recebe 404 (não 403, pra não confirmar que o ID existe)."""

    def get(self, request, pk):
        donation = get_object_or_404(Donation, pk=pk, status=Donation.Status.PAID)
        is_owner = donation.person_id and request.user.person_id == donation.person_id
        if not (is_owner or request.user.can_manage_people):
            raise Http404
        pdf_bytes = generate_donation_receipt_pdf(donation.church, donation)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="recibo-doacao-{donation.pk}.pdf"'
        response["Cache-Control"] = "private, no-store"
        return response


class DREView(IsChurchManagerMixin, View):
    """DRE do período — tela com o breakdown OU o PDF direto
    (`?formato=pdf`), mesmo par tela+PDF de sempre nesse app."""

    template_name = "finance/dre.html"

    def get(self, request):
        inicio, fim = _periodo_do_get(request)
        if request.GET.get("formato") == "pdf":
            pdf_bytes = generate_dre_pdf(request.church, inicio, fim)
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="dre-{inicio}-a-{fim}.pdf"'
            response["Cache-Control"] = "private, no-store"
            return response
        breakdown = dre_breakdown(request.church, inicio, fim)
        return render(request, self.template_name, {"breakdown": breakdown, "inicio": inicio, "fim": fim})


class BalanceteView(IsChurchManagerMixin, View):
    """"Balancete" simplificado (saldo acumulado por mês) — ver
    docstring de `finance.dre.saldo_acumulado` pro porquê do nome entre
    aspas (não é um balanço patrimonial contábil de verdade)."""

    template_name = "finance/balancete.html"

    def get(self, request):
        inicio, fim = _periodo_do_get(request)
        if request.GET.get("formato") == "pdf":
            pdf_bytes = generate_balancete_pdf(request.church, inicio, fim)
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="balancete-{inicio}-a-{fim}.pdf"'
            response["Cache-Control"] = "private, no-store"
            return response
        dados = saldo_acumulado(request.church, inicio, fim)
        return render(request, self.template_name, {"dados": dados, "inicio": inicio, "fim": fim})
