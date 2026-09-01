from django.urls import path

from sermons.views import SermonCreateView, SermonDeleteView, SermonListView, SermonUpdateView

app_name = "sermons"

urlpatterns = [
    path("", SermonListView.as_view(), name="list"),
    path("novo/", SermonCreateView.as_view(), name="create"),
    path("<int:pk>/editar/", SermonUpdateView.as_view(), name="update"),
    path("<int:pk>/excluir/", SermonDeleteView.as_view(), name="delete"),
]
