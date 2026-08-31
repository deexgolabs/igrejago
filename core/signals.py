from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.middleware import get_current_user
from core.models import AuditLog
from core.tenant_context import get_current_church


def _log(instance, action):
    user = get_current_user()
    # A igreja do próprio registro auditado (se ele tiver uma — todo
    # `TenantModel` tem) é mais confiável do que o thread-local: cobre
    # também comandos/cron que processam várias igrejas (`tenant_context`
    # já deixaria o thread-local certo, mas usar `instance.church` direto
    # dispensa depender disso aqui).
    church = getattr(instance, "church", None) or get_current_church()
    if church is None:
        return  # sem igreja pra associar (ex.: instância órfã) — não audita
    AuditLog.objects.create(
        church=church,
        user=user if user and user.is_authenticated else None,
        action=action,
        model_name=instance.__class__.__name__,
        object_repr=str(instance)[:255],
        object_id=str(instance.pk),
    )


def register_audit_log(model):
    """Conecta um model aos sinais de criação/edição/exclusão. Chamado uma
    vez por model em `CoreConfig.ready()` — não é automático pra todo
    model do projeto de propósito (ex.: `AuditLog` mesmo, ou sessões, não
    fazem sentido serem auditados)."""

    @receiver(post_save, sender=model, weak=False)
    def _on_save(sender, instance, created, **kwargs):
        _log(instance, AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE)

    @receiver(post_delete, sender=model, weak=False)
    def _on_delete(sender, instance, **kwargs):
        _log(instance, AuditLog.Action.DELETE)
