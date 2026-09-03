def pending_counts(request):
    """Contador de "cadastro pendente"/"aguardando atendimento humano"
    pro badge no menu (`templates/base.html`) — só consulta o banco pra
    quem realmente vê esses links (`is_unrestricted_manager` com igreja
    resolvida); membro, visitante e página pública não pagam a query."""
    user = getattr(request, "user", None)
    church = getattr(request, "church", None)
    if not (user and user.is_authenticated and getattr(user, "is_unrestricted_manager", False) and church):
        return {}

    from assistant.models import Conversation, PersonDraft

    return {
        "assistant_draft_count": PersonDraft.objects.filter(status=PersonDraft.Status.PENDING).count(),
        "assistant_human_queue_count": Conversation.objects.filter(state=Conversation.State.AGUARDANDO_HUMANO).count(),
    }
