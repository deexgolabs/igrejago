from django.conf import settings
from django.db import models
from django.urls import reverse

from core.tenancy import TenantModel


class BioPage(TenantModel):
    """Página pública estilo Linktree. Modelada como tabela (em vez de
    singleton fixo) para já comportar múltiplas páginas no futuro (ex.: uma
    por congregação/campus), mas hoje o fluxo usa sempre a primeira ativa
    de cada igreja. `slug` é único POR IGREJA — a página pública mora em
    `<slug:church_slug>/links/<slug:slug>/`."""

    slug = models.SlugField("Slug", max_length=50, default="links")
    church_name = models.CharField("Nome da igreja", max_length=150)
    headline = models.CharField("Frase de destaque", max_length=200, blank=True)
    avatar = models.ImageField("Foto/logo", upload_to="linkbio/avatars/", blank=True, null=True)
    background_color = models.CharField("Cor de fundo", max_length=7, default="#0f172a")
    accent_color = models.CharField("Cor de destaque", max_length=7, default="#38bdf8")
    is_active = models.BooleanField("Ativa", default=True)

    class Meta:
        verbose_name = "Página de links"
        verbose_name_plural = "Páginas de links"
        constraints = [
            models.UniqueConstraint(fields=["church", "slug"], name="unique_biopage_slug_per_church"),
        ]

    def __str__(self):
        return self.church_name

    def get_absolute_url(self):
        return reverse("linkbio_public:page", args=[self.church.slug, self.slug])

    @property
    def short_link(self):
        """`ShortLink` apontando pra esta página, se algum pastor/secretaria
        já criou um (ver `core.ShortLink` — atalho "criar link curto" em
        `linkbio/manage.html`). `None` quando ainda não existe."""
        from core.models import ShortLink

        return ShortLink.objects.filter(target_path=self.get_absolute_url()).first()

    @property
    def public_url(self):
        """URL pública completa mostrada em "Link na Bio" — usa o link
        curto (`igrejago.link/<slug>`) se um `ShortLink` já foi criado
        apontando pra esta página; senão cai pro domínio espelho
        (`PUBLIC_LINK_DOMAIN`) longo de sempre; em branco (dev, sem
        domínio espelho), continua só o caminho relativo."""
        short = self.short_link
        if short:
            return short.full_url
        path = self.get_absolute_url()
        return f"{settings.PUBLIC_LINK_DOMAIN}{path}" if settings.PUBLIC_LINK_DOMAIN else path


class Link(TenantModel):
    """Um botão da página de links (rede social, culto ao vivo, doação,
    evento etc.). `order` controla a posição; reordenar é só editar esse
    número (ou arrastar na UI, via HTMX, mais adiante)."""

    class LinkType(models.TextChoices):
        SOCIAL = "SOCIAL", "Rede social"
        LIVE = "LIVE", "Culto ao vivo"
        DONATION = "DONATION", "Doação"
        EVENT = "EVENT", "Evento"
        OTHER = "OTHER", "Outro"

    page = models.ForeignKey(
        BioPage, on_delete=models.CASCADE, related_name="links", verbose_name="Página"
    )
    title = models.CharField("Título", max_length=100)
    url = models.URLField("URL")
    link_type = models.CharField(
        "Tipo", max_length=10, choices=LinkType.choices, default=LinkType.OTHER
    )
    icon = models.CharField(
        "Ícone", max_length=50, blank=True,
        help_text="Nome do ícone (ex.: 'instagram', 'youtube') usado no template.",
    )
    order = models.PositiveIntegerField("Ordem", default=0)
    is_active = models.BooleanField("Ativo", default=True)
    click_count = models.PositiveIntegerField("Cliques", default=0, editable=False)

    class Meta:
        verbose_name = "Link"
        verbose_name_plural = "Links"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.title} ({self.page.church_name})"
