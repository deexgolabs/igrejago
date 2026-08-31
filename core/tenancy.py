"""Multi-tenência por linha (uma coluna `church_id` em cada tabela),
mesmo mecanismo já validado no projeto irmão crm-odonto: um manager
customizado filtra `Model.objects` sozinho pela igreja "atual" (lida de
`core.tenant_context`, preenchida por `TenantMiddleware` a cada
requisição). Nenhuma view/query de código de aplicação precisa lembrar
de filtrar por igreja manualmente — é opt-OUT (usar `todas_as_igrejas`
explicitamente), não opt-in, porque esquecer de filtrar é o tipo de erro
que vaza dado de uma igreja pra outra."""

from django.db import models

from core.tenant_context import get_current_church, set_current_church


class TenantQuerySet(models.QuerySet):
    def da_igreja(self, church):
        return self.filter(church=church)


class TenantManager(models.Manager):
    """`Model.objects.all()` só devolve as linhas da igreja atual — fora
    de uma requisição (comando/shell) ou pra quem não tem igreja (dono da
    plataforma), não filtra nada. Use `Model.todas_as_igrejas` pra uma
    consulta explicitamente sem filtro DENTRO de uma requisição (páginas
    públicas, que resolvem a igreja pelo slug da URL, não pelo usuário
    logado) — nunca use isso por atalho em código autenticado comum."""

    def get_queryset(self):
        qs = TenantQuerySet(self.model, using=self._db)
        church = get_current_church()
        return qs if church is None else qs.filter(church=church)


class TenantFormMixin:
    """Pra qualquer `CreateView` de um `TenantModel` — seta `church` na
    instância antes de salvar (o campo é obrigatório, sem default; sem
    isso o `form.save()` do Django quebraria). Liste ANTES de `CreateView`
    nas bases da view (`class XCreateView(TenantFormMixin, CreateView)`)
    pra entrar certo na cadeia de `super().form_valid()` — se a própria
    view já sobrescreve `form_valid` (comum aqui, pra setar `created_by`),
    ela continua funcionando normal desde que termine com
    `return super().form_valid(form)`, como já é a convenção do projeto."""

    def form_valid(self, form):
        form.instance.church = self.request.church
        return super().form_valid(form)


class PublicChurchMixin:
    """Pra views PÚBLICAS (sem login) de um `TenantModel` — a igreja não
    vem do usuário logado (não tem), vem do slug na própria URL
    (`<slug:church_slug>/...`). Resolve em `self.church` e põe tanto em
    `request.church` (pros templates — o mesmo `church_config` de sempre,
    via `core.context_processors.church_config`) quanto no thread-local
    (pro `TenantManager` filtrar `Model.objects` normal dentro da view,
    sem precisar de `todas_as_igrejas` espalhado pelo código público) —
    ANTES de `get()`/`post()` rodar. Combine com `TenantFormMixin` (que já
    lê `self.request.church`) pra criar registro público sem repetir
    lógica."""

    def dispatch(self, request, *args, **kwargs):
        from django.shortcuts import get_object_or_404

        from core.models import Church

        self.church = get_object_or_404(Church, slug=kwargs["church_slug"])
        request.church = self.church
        set_current_church(self.church)
        return super().dispatch(request, *args, **kwargs)


class TenantModel(models.Model):
    """Base abstrata pra qualquer model que pertence a uma igreja.
    `church` é uma FK direta em CADA model, mesmo quando dá pra chegar na
    igreja por um FK pai (ex.: `FormAnswer` → `FormResponse` →
    `CustomForm`) — é assim que o manager consegue filtrar igual em
    qualquer tabela, sem exceção pras "tabelas-filha"."""

    church = models.ForeignKey("core.Church", on_delete=models.CASCADE, verbose_name="Igreja")

    objects = TenantManager()
    todas_as_igrejas = models.Manager()

    class Meta:
        abstract = True
