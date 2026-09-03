from django.urls import path

from assistant.views import (
    ConversationHumanQueueListView,
    PersonDraftApproveView,
    PersonDraftListView,
    PersonDraftRejectView,
    PersonUpdateFormView,
)

app_name = "assistant"

urlpatterns = [
    path("cadastros-pendentes/", PersonDraftListView.as_view(), name="draft_list"),
    path("cadastros-pendentes/<int:pk>/aprovar/", PersonDraftApproveView.as_view(), name="draft_approve"),
    path("cadastros-pendentes/<int:pk>/rejeitar/", PersonDraftRejectView.as_view(), name="draft_reject"),
    path("aguardando-atendimento/", ConversationHumanQueueListView.as_view(), name="human_queue"),
    # Pública, sem login, sem slug de igreja — igreja implícita pelo token (mesmo padrão de escalas.ConfirmarEscalaView)
    path("atualizar-cadastro/<uuid:token>/", PersonUpdateFormView.as_view(), name="person_update_form"),
]
