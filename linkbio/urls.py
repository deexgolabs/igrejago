from django.urls import path

from linkbio.views import (
    BioPageManageView,
    LinkCreateView,
    LinkDeleteView,
    LinkMoveView,
    LinkUpdateView,
)

app_name = "linkbio"

urlpatterns = [
    path("admin/", BioPageManageView.as_view(), name="manage"),
    path("admin/links/novo/", LinkCreateView.as_view(), name="link_create"),
    path("admin/links/<int:pk>/editar/", LinkUpdateView.as_view(), name="link_update"),
    path("admin/links/<int:pk>/excluir/", LinkDeleteView.as_view(), name="link_delete"),
    path("admin/links/<int:pk>/mover/<str:direction>/", LinkMoveView.as_view(), name="link_move"),
]
