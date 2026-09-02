from django.urls import path

from api.views import EventListAPIView, PersonListAPIView, RegistrationListAPIView, TransactionListAPIView

app_name = "api"

urlpatterns = [
    path("pessoas/", PersonListAPIView.as_view(), name="pessoas"),
    path("doacoes/", TransactionListAPIView.as_view(), name="doacoes"),
    path("eventos/", EventListAPIView.as_view(), name="eventos"),
    path("inscricoes/", RegistrationListAPIView.as_view(), name="inscricoes"),
]
