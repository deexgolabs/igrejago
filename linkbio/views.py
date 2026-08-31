from django.contrib import messages
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, UpdateView, View

from accounts.mixins import CanManagePeopleMixin
from core.models import Church
from linkbio.forms import BioPageForm, LinkForm
from linkbio.models import BioPage, Link


def bio_page(request, church_slug, slug="links"):
    """Página pública estilo Linktree — sem login. A igreja vem do slug na
    URL (mesmo padrão de `core.tenancy.PublicChurchMixin`, mas escrito à
    mão aqui porque é uma view baseada em função, não em classe) — usa
    `todas_as_igrejas` porque não há usuário logado pra popular o
    thread-local."""
    church = get_object_or_404(Church, slug=church_slug)
    page = get_object_or_404(BioPage.todas_as_igrejas, church=church, slug=slug, is_active=True)
    links = page.links.filter(is_active=True)
    request.church = church
    return render(request, "linkbio/bio_page.html", {"page": page, "links": links})


def link_click(request, church_slug, pk):
    """Todo clique num link público passa por aqui antes de redirecionar —
    é o único jeito de `Link.click_count` (que já existia no model) virar
    um número real em vez de ficar sempre zerado. `F("click_count") + 1`
    evita a race condition de um `read-modify-write` normal se dois
    cliques chegarem quase juntos."""
    church = get_object_or_404(Church, slug=church_slug)
    link = get_object_or_404(Link.todas_as_igrejas, pk=pk, church=church, is_active=True)
    Link.todas_as_igrejas.filter(pk=pk, church=church).update(click_count=F("click_count") + 1)
    return redirect(link.url)


def _get_default_page(church):
    page, _ = BioPage.objects.get_or_create(
        church=church, slug="links", defaults={"church_name": church.name}
    )
    return page


class BioPageManageView(CanManagePeopleMixin, View):
    """Painel único: editar os dados da página (nome/cores/avatar) + gerenciar
    a lista de links. Assume uma só BioPage (slug="links") por igreja, já
    que o sistema ainda é single-tenant."""

    template_name = "linkbio/manage.html"

    def get(self, request):
        page = _get_default_page(request.church)
        form = BioPageForm(instance=page)
        links = page.links.order_by("order", "id")
        return render(request, self.template_name, {"page": page, "form": form, "links": links})

    def post(self, request):
        page = _get_default_page(request.church)
        form = BioPageForm(request.POST, request.FILES, instance=page)
        if form.is_valid():
            form.save()
            messages.success(request, "Página atualizada com sucesso.")
            return redirect("linkbio:manage")
        links = page.links.order_by("order", "id")
        return render(request, self.template_name, {"page": page, "form": form, "links": links})


class LinkCreateView(CanManagePeopleMixin, CreateView):
    model = Link
    form_class = LinkForm
    template_name = "linkbio/link_form.html"
    success_url = reverse_lazy("linkbio:manage")

    def form_valid(self, form):
        page = _get_default_page(self.request.church)
        form.instance.page = page
        form.instance.church = page.church
        form.instance.order = (page.links.count() + 1) * 10
        messages.success(self.request, "Link adicionado.")
        return super().form_valid(form)


class LinkUpdateView(CanManagePeopleMixin, UpdateView):
    model = Link
    form_class = LinkForm
    template_name = "linkbio/link_form.html"
    success_url = reverse_lazy("linkbio:manage")

    def form_valid(self, form):
        messages.success(self.request, "Link atualizado.")
        return super().form_valid(form)


class LinkDeleteView(CanManagePeopleMixin, View):
    def post(self, request, pk):
        link = get_object_or_404(Link, pk=pk)
        link.delete()
        messages.success(request, "Link removido.")
        return redirect("linkbio:manage")


class LinkMoveView(CanManagePeopleMixin, View):
    """Troca a posição (`order`) do link com o vizinho acima/abaixo — dá o
    efeito de reordenar sem precisar de drag-and-drop/JS."""

    def post(self, request, pk, direction):
        link = get_object_or_404(Link, pk=pk)
        siblings = list(link.page.links.order_by("order", "id"))
        index = siblings.index(link)

        if direction == "up" and index > 0:
            neighbor = siblings[index - 1]
        elif direction == "down" and index < len(siblings) - 1:
            neighbor = siblings[index + 1]
        else:
            neighbor = None

        if neighbor:
            link.order, neighbor.order = neighbor.order, link.order
            link.save(update_fields=["order"])
            neighbor.save(update_fields=["order"])

        return redirect("linkbio:manage")
