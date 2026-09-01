from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from accounts.mixins import CanManagePeopleMixin
from checkin.forms import CheckinForm, SalaInfantilForm
from checkin.models import Checkin, SalaInfantil
from core.qr import qr_data_uri
from core.tenancy import TenantFormMixin


class SalaInfantilListView(CanManagePeopleMixin, ListView):
    model = SalaInfantil
    template_name = "checkin/sala_list.html"
    context_object_name = "salas"


class SalaInfantilCreateView(TenantFormMixin, CanManagePeopleMixin, CreateView):
    model = SalaInfantil
    form_class = SalaInfantilForm
    template_name = "checkin/sala_form.html"
    success_url = reverse_lazy("checkin:sala_list")

    def form_valid(self, form):
        messages.success(self.request, "Sala criada.")
        return super().form_valid(form)


class SalaInfantilUpdateView(CanManagePeopleMixin, UpdateView):
    model = SalaInfantil
    form_class = SalaInfantilForm
    template_name = "checkin/sala_form.html"
    success_url = reverse_lazy("checkin:sala_list")

    def form_valid(self, form):
        messages.success(self.request, "Sala atualizada.")
        return super().form_valid(form)


class SalaInfantilDeleteView(CanManagePeopleMixin, DeleteView):
    model = SalaInfantil
    template_name = "checkin/sala_confirm_delete.html"
    success_url = reverse_lazy("checkin:sala_list")

    def form_valid(self, form):
        messages.success(self.request, "Sala removida.")
        return super().form_valid(form)


class CheckinListView(CanManagePeopleMixin, ListView):
    """Painel do dia — quem está com check-in ativo agora."""

    model = Checkin
    template_name = "checkin/checkin_list.html"
    context_object_name = "checkins"

    def get_queryset(self):
        return Checkin.objects.filter(checked_in_at__date=date.today()).select_related("sala")


class CheckinCreateView(CanManagePeopleMixin, View):
    template_name = "checkin/checkin_form.html"

    def get(self, request):
        form = CheckinForm()
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = CheckinForm(request.POST)
        if form.is_valid():
            checkin = form.save(commit=False)
            checkin.church = request.church
            checkin.checked_in_by = request.user
            checkin.save()
            messages.success(request, "Check-in registrado.")
            return redirect("checkin:etiqueta", pk=checkin.pk)
        return render(request, self.template_name, {"form": form})


class CheckinEtiquetaView(CanManagePeopleMixin, View):
    """Tela pra imprimir as duas etiquetas (criança + responsável) —
    `@media print` no template, sem gerar PDF: é só imprimir a própria
    página."""

    template_name = "checkin/checkin_etiqueta.html"

    def get(self, request, pk):
        checkin = get_object_or_404(Checkin, pk=pk)
        url = request.build_absolute_uri(reverse("checkin:detalhe", args=[checkin.checkin_token]))
        context = {
            "checkin": checkin,
            "qr_data_uri": qr_data_uri(url),
            "medical_notes": checkin.child.medical_notes if checkin.child else "",
        }
        return render(request, self.template_name, context)


class CheckinBuscarView(CanManagePeopleMixin, View):
    """Busca por código de retirada, pra confirmar o check-out."""

    template_name = "checkin/checkin_buscar.html"

    def get(self, request):
        code = request.GET.get("code", "").strip()
        checkin = None
        if code:
            checkin = (
                Checkin.objects.filter(pickup_code=code, checked_out_at__isnull=True)
                .order_by("-checked_in_at")
                .first()
            )
            if checkin is None:
                messages.error(request, "Código não encontrado ou já retirado.")
        return render(request, self.template_name, {"checkin": checkin, "code": code})


class CheckinDetalheView(CanManagePeopleMixin, View):
    """Endpoint que o QR code da etiqueta aponta — mesmo padrão de
    `events.RegistrationCheckInView`: só encoda essa URL, quem escaneia é
    sempre um membro da equipe já logado no navegador do próprio aparelho."""

    template_name = "checkin/checkin_detalhe.html"

    def get(self, request, token):
        checkin = get_object_or_404(Checkin, checkin_token=token)
        return render(request, self.template_name, {"checkin": checkin})


class CheckinCheckoutView(CanManagePeopleMixin, View):
    def post(self, request, pk):
        checkin = get_object_or_404(Checkin, pk=pk, checked_out_at__isnull=True)
        checkin.checked_out_at = timezone.now()
        checkin.checked_out_by = request.user
        checkin.save(update_fields=["checked_out_at", "checked_out_by"])
        messages.success(request, f"Check-out de {checkin.child_name} confirmado.")
        return redirect("checkin:lista")
