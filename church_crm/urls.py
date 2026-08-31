from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("pessoas/", include("people.urls")),
    path("eventos/", include("events.urls")),
    path("links/", include("linkbio.urls")),
    path("financeiro/", include("finance.urls")),
    path("celulas/", include("cells.urls")),
    path("mensagens/", include("notifications.urls")),
    path("formularios/", include("custom_forms.urls")),

    # Páginas públicas (sem login) — a igreja é resolvida pelo próprio
    # slug na URL (`core.tenancy.PublicChurchMixin` ou equivalente manual
    # em views baseadas em função), nunca pelo usuário logado. Mesmo
    # `app_name` do include de gestão de cada app — os nomes de rota não
    # se sobrepõem entre os dois arquivos (`urls.py` × `public_urls.py`).
    path("<slug:church_slug>/pessoas/", include("people.public_urls")),
    path("<slug:church_slug>/eventos/", include("events.public_urls")),
    path("<slug:church_slug>/links/", include("linkbio.public_urls")),
    path("<slug:church_slug>/formularios/", include("custom_forms.public_urls")),

    path("", include("core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
