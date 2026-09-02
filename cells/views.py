from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView, View

from accounts.mixins import CanManageCellsMixin
from cells.forms import CellForm, CellMeetingForm
from cells.models import Cell
from core.tenancy import TenantFormMixin


def _cells_escopadas(user):
    """`Cell.objects` (todas) pra Pastor/Secretaria/Líder de Departamento;
    só a(s) própria(s) célula(s) pra quem é SÓ líder de célula (`role`
    pode até ser Membro — ver `User.is_cell_leader`)."""
    if user.is_unrestricted_manager or user.is_department_leader:
        return Cell.objects.all()
    return Cell.objects.filter(leader=user.person)


class CellListView(CanManageCellsMixin, ListView):
    model = Cell
    template_name = "cells/cell_list.html"
    context_object_name = "cells"

    def get_queryset(self):
        return _cells_escopadas(self.request.user)


class CellDetailView(CanManageCellsMixin, DetailView):
    model = Cell
    template_name = "cells/cell_detail.html"
    context_object_name = "cell"

    def get_queryset(self):
        return _cells_escopadas(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meetings"] = self.object.meetings.order_by("-date")[:20]
        return context


class CellCreateView(TenantFormMixin, CanManageCellsMixin, CreateView):
    model = Cell
    form_class = CellForm
    template_name = "cells/cell_form.html"

    def form_valid(self, form):
        messages.success(self.request, "Célula criada.")
        return super().form_valid(form)


class CellUpdateView(CanManageCellsMixin, UpdateView):
    model = Cell
    form_class = CellForm
    template_name = "cells/cell_form.html"

    def get_queryset(self):
        return _cells_escopadas(self.request.user)

    def form_valid(self, form):
        messages.success(self.request, "Célula atualizada.")
        return super().form_valid(form)


class CellDeleteView(CanManageCellsMixin, DeleteView):
    model = Cell
    template_name = "cells/cell_confirm_delete.html"
    success_url = reverse_lazy("cells:list")

    def get_queryset(self):
        return _cells_escopadas(self.request.user)

    def form_valid(self, form):
        messages.success(self.request, "Célula removida.")
        return super().form_valid(form)


class CellMeetingCreateView(CanManageCellsMixin, View):
    """Registra a presença de uma reunião semanal da célula."""

    template_name = "cells/meeting_form.html"

    def get(self, request, pk):
        cell = get_object_or_404(_cells_escopadas(request.user), pk=pk)
        form = CellMeetingForm(cell=cell)
        return render(request, self.template_name, {"cell": cell, "form": form})

    def post(self, request, pk):
        cell = get_object_or_404(_cells_escopadas(request.user), pk=pk)
        form = CellMeetingForm(request.POST, cell=cell)
        if form.is_valid():
            meeting = form.save(commit=False)
            meeting.cell = cell
            meeting.church = cell.church
            meeting.save()
            form.save_m2m()
            messages.success(request, "Presença registrada.")
            return redirect("cells:detail", pk=cell.pk)
        return render(request, self.template_name, {"cell": cell, "form": form})
