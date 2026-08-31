from django.urls import path

from cells.views import (
    CellCreateView,
    CellDeleteView,
    CellDetailView,
    CellListView,
    CellMeetingCreateView,
    CellUpdateView,
)

app_name = "cells"

urlpatterns = [
    path("", CellListView.as_view(), name="list"),
    path("novo/", CellCreateView.as_view(), name="create"),
    path("<int:pk>/", CellDetailView.as_view(), name="detail"),
    path("<int:pk>/editar/", CellUpdateView.as_view(), name="update"),
    path("<int:pk>/excluir/", CellDeleteView.as_view(), name="delete"),
    path("<int:pk>/reuniao/nova/", CellMeetingCreateView.as_view(), name="meeting_create"),
]
