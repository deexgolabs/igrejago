from django.urls import path

from api.views import (
    EventListAPIView,
    PersonDetailAPIView,
    PersonListCreateAPIView,
    RegistrationListAPIView,
    TransactionListAPIView,
)

app_name = "api"

urlpatterns = [
    path("pessoas/", PersonListCreateAPIView.as_view(), name="pessoas"),
    path("pessoas/<int:pk>/", PersonDetailAPIView.as_view(), name="pessoa_detail"),
    path("doacoes/", TransactionListAPIView.as_view(), name="doacoes"),
    path("eventos/", EventListAPIView.as_view(), name="eventos"),
    path("inscricoes/", RegistrationListAPIView.as_view(), name="inscricoes"),
]
