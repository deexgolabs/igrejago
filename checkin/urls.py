from django.urls import path

from checkin.views import (
    CheckinBuscarView,
    CheckinCheckoutView,
    CheckinCreateView,
    CheckinDetalheView,
    CheckinEtiquetaView,
    CheckinListView,
    SalaInfantilCreateView,
    SalaInfantilDeleteView,
    SalaInfantilListView,
    SalaInfantilUpdateView,
)

app_name = "checkin"

urlpatterns = [
    path("", CheckinListView.as_view(), name="lista"),
    path("novo/", CheckinCreateView.as_view(), name="create"),
    path("<int:pk>/etiqueta/", CheckinEtiquetaView.as_view(), name="etiqueta"),
    path("<int:pk>/checkout/", CheckinCheckoutView.as_view(), name="checkout"),
    path("buscar/", CheckinBuscarView.as_view(), name="buscar"),
    path("detalhe/<uuid:token>/", CheckinDetalheView.as_view(), name="detalhe"),
    path("salas/", SalaInfantilListView.as_view(), name="sala_list"),
    path("salas/nova/", SalaInfantilCreateView.as_view(), name="sala_create"),
    path("salas/<int:pk>/editar/", SalaInfantilUpdateView.as_view(), name="sala_update"),
    path("salas/<int:pk>/excluir/", SalaInfantilDeleteView.as_view(), name="sala_delete"),
]
