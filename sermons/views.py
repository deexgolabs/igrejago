from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from accounts.mixins import CanManagePeopleMixin
from core.tenancy import PublicChurchMixin, TenantFormMixin
from sermons.forms import SermonForm
from sermons.models import Sermon


class SermonListView(CanManagePeopleMixin, ListView):
    model = Sermon
    template_name = "sermons/sermon_manage_list.html"
    context_object_name = "sermons"


class SermonCreateView(TenantFormMixin, CanManagePeopleMixin, CreateView):
    model = Sermon
    form_class = SermonForm
    template_name = "sermons/sermon_form.html"
    success_url = reverse_lazy("sermons:list")

    def form_valid(self, form):
        messages.success(self.request, "Sermão cadastrado.")
        return super().form_valid(form)


class SermonUpdateView(CanManagePeopleMixin, UpdateView):
    model = Sermon
    form_class = SermonForm
    template_name = "sermons/sermon_form.html"
    success_url = reverse_lazy("sermons:list")

    def form_valid(self, form):
        messages.success(self.request, "Sermão atualizado.")
        return super().form_valid(form)


class SermonDeleteView(CanManagePeopleMixin, DeleteView):
    model = Sermon
    template_name = "sermons/sermon_confirm_delete.html"
    success_url = reverse_lazy("sermons:list")

    def form_valid(self, form):
        messages.success(self.request, "Sermão removido.")
        return super().form_valid(form)


class SermonPublicListView(PublicChurchMixin, ListView):
    """Página pública `<slug>/sermoes/` — mesmo padrão de `<slug>/links/`
    (`PublicChurchMixin` resolve a igreja pelo slug da URL). `Sermon.objects`
    já vem filtrado pela igreja certa (thread-local setado pelo mixin)."""

    model = Sermon
    template_name = "sermons/sermon_public_list.html"
    context_object_name = "sermons"

    def get_queryset(self):
        return Sermon.objects.filter(is_published=True)
