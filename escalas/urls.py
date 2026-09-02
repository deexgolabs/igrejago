from django.urls import path

from escalas.views import (
    AceitarTrocaView,
    ConfirmarEscalaView,
    EscalaCalendarioView,
    EscalaCreateView,
    EscalaDeleteView,
    EscalaDetailView,
    EscalaUpdateView,
    MinhaIndisponibilidadeDeleteView,
    MinhaIndisponibilidadeListView,
    PedirTrocaView,
)

app_name = "escalas"

urlpatterns = [
    path("", EscalaCalendarioView.as_view(), name="calendario"),
    path("nova/", EscalaCreateView.as_view(), name="create"),
    path("minha-disponibilidade/", MinhaIndisponibilidadeListView.as_view(), name="minha_indisponibilidade"),
    path(
        "minha-disponibilidade/<int:pk>/excluir/",
        MinhaIndisponibilidadeDeleteView.as_view(),
        name="minha_indisponibilidade_delete",
    ),
    path("<int:pk>/", EscalaDetailView.as_view(), name="detail"),
    path("<int:pk>/editar/", EscalaUpdateView.as_view(), name="update"),
    path("<int:pk>/excluir/", EscalaDeleteView.as_view(), name="delete"),
    path("confirmar/<uuid:token>/", ConfirmarEscalaView.as_view(), name="confirmar"),
    path("confirmar/<uuid:token>/pedir-troca/", PedirTrocaView.as_view(), name="pedir_troca"),
    path("trocar/<uuid:token>/", AceitarTrocaView.as_view(), name="aceitar_troca"),
]
