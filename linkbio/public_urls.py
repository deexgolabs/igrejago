"""Rotas PÚBLICAS (sem login) de `linkbio`, montadas sob
`<slug:church_slug>/links/` em `church_crm/urls.py`. Views baseadas em
função (não em classe), por isso resolvem a igreja à mão em vez de usar
`core.tenancy.PublicChurchMixin`. `app_name` PRÓPRIO (`linkbio_public`,
diferente de `linkbio/urls.py`) — dois `include()` com o MESMO `app_name`
fazem o Django só conseguir reverter (`reverse()`/`{% url %}`) as rotas do
primeiro registrado, silenciosamente quebrando as do segundo (confirmado
na prática: ver `django.urls.get_resolver().namespace_dict`)."""

from django.urls import path

from linkbio.views import bio_page, link_click

app_name = "linkbio_public"

urlpatterns = [
    path("click/<int:pk>/", link_click, name="click"),
    path("", bio_page, name="default"),
    path("<slug:slug>/", bio_page, name="page"),
]
