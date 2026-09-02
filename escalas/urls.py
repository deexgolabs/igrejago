from django.urls import path

from escalas.views import (
    AceitarTrocaView,
    ConfirmarEscalaView,
    EscalaCalendarioView,
    EscalaCreateView,
    EscalaDeleteView,
    EscalaDetailView,
    EscalaSongAddView,
    EscalaSongRemoveView,
    EscalaUpdateView,
    MinhaIndisponibilidadeDeleteView,
    MinhaIndisponibilidadeListView,
    PedirTrocaView,
    ServiceOrderItemAddView,
    ServiceOrderItemRemoveView,
    SongCreateView,
    SongDeleteView,
    SongListView,
    SongUpdateView,
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
    path("musicas/", SongListView.as_view(), name="song_list"),
    path("musicas/nova/", SongCreateView.as_view(), name="song_create"),
    path("musicas/<int:pk>/editar/", SongUpdateView.as_view(), name="song_update"),
    path("musicas/<int:pk>/excluir/", SongDeleteView.as_view(), name="song_delete"),
    path("<int:pk>/", EscalaDetailView.as_view(), name="detail"),
    path("<int:pk>/editar/", EscalaUpdateView.as_view(), name="update"),
    path("<int:pk>/excluir/", EscalaDeleteView.as_view(), name="delete"),
    path("<int:pk>/repertorio/adicionar/", EscalaSongAddView.as_view(), name="song_add"),
    path("<int:pk>/repertorio/<int:escala_song_pk>/remover/", EscalaSongRemoveView.as_view(), name="song_remove"),
    path("<int:pk>/ordem-culto/adicionar/", ServiceOrderItemAddView.as_view(), name="order_item_add"),
    path("<int:pk>/ordem-culto/<int:item_pk>/remover/", ServiceOrderItemRemoveView.as_view(), name="order_item_remove"),
    path("confirmar/<uuid:token>/", ConfirmarEscalaView.as_view(), name="confirmar"),
    path("confirmar/<uuid:token>/pedir-troca/", PedirTrocaView.as_view(), name="pedir_troca"),
    path("trocar/<uuid:token>/", AceitarTrocaView.as_view(), name="aceitar_troca"),
]
