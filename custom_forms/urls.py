from django.urls import path

from custom_forms.views import (
    CustomFormCreateView,
    CustomFormDeleteView,
    CustomFormDuplicateView,
    CustomFormFromStarterView,
    CustomFormListView,
    CustomFormUpdateView,
    FormFieldDeleteView,
    FormFieldListView,
    FormFieldUpdateView,
    FormResponseExportView,
    FormResponseListView,
)

app_name = "custom_forms"

urlpatterns = [
    path("", CustomFormListView.as_view(), name="list"),
    path("novo/", CustomFormCreateView.as_view(), name="create"),
    path("modelos/novo/", CustomFormFromStarterView.as_view(), name="from_starter"),
    path("<int:pk>/editar/", CustomFormUpdateView.as_view(), name="update"),
    path("<int:pk>/excluir/", CustomFormDeleteView.as_view(), name="delete"),
    path("<int:pk>/duplicar/", CustomFormDuplicateView.as_view(), name="duplicate"),
    path("<int:pk>/campos/", FormFieldListView.as_view(), name="field_list"),
    path("<int:pk>/campos/<int:field_pk>/editar/", FormFieldUpdateView.as_view(), name="field_update"),
    path("<int:pk>/campos/<int:field_pk>/excluir/", FormFieldDeleteView.as_view(), name="field_delete"),
    path("<int:pk>/respostas/", FormResponseListView.as_view(), name="response_list"),
    path("<int:pk>/respostas/exportar/", FormResponseExportView.as_view(), name="response_export"),
]
