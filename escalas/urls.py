from django.urls import path

from escalas.views import (
    ConfirmarEscalaView,
    EscalaCalendarioView,
    EscalaCreateView,
    EscalaDeleteView,
    EscalaDetailView,
    EscalaUpdateView,
)

app_name = "escalas"

urlpatterns = [
    path("", EscalaCalendarioView.as_view(), name="calendario"),
    path("nova/", EscalaCreateView.as_view(), name="create"),
    path("<int:pk>/", EscalaDetailView.as_view(), name="detail"),
    path("<int:pk>/editar/", EscalaUpdateView.as_view(), name="update"),
    path("<int:pk>/excluir/", EscalaDeleteView.as_view(), name="delete"),
    path("confirmar/<uuid:token>/", ConfirmarEscalaView.as_view(), name="confirmar"),
]
