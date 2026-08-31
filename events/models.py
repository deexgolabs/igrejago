import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from core.tenancy import TenantModel


class Event(TenantModel):
    """Evento da igreja (acampamento, conferência, curso). Pode ser gratuito
    ou pago; quando pago, o pagamento em si é tratado na Registration."""

    class EventStatus(models.TextChoices):
        DRAFT = "DRAFT", "Rascunho"
        PUBLISHED = "PUBLISHED", "Publicado"
        CLOSED = "CLOSED", "Encerrado"

    title = models.CharField("Título", max_length=200)
    slug = models.SlugField("Slug", max_length=220, blank=True)
    description = models.TextField("Descrição", blank=True)
    image = models.ImageField("Imagem de capa", upload_to="events/covers/", blank=True, null=True)

    location = models.CharField("Local", max_length=255, blank=True)
    start_datetime = models.DateTimeField("Início")
    end_datetime = models.DateTimeField("Término", blank=True, null=True)

    is_paid = models.BooleanField("Evento pago", default=False)
    price = models.DecimalField(
        "Valor (R$)", max_digits=8, decimal_places=2, default=0,
        help_text="Ignorado quando o evento é gratuito.",
    )
    capacity = models.PositiveIntegerField(
        "Vagas", blank=True, null=True, help_text="Deixe em branco para vagas ilimitadas."
    )

    brand_color = models.CharField(
        "Cor do evento", max_length=7, blank=True,
        help_text="Deixe em branco para usar a cor padrão da igreja na página pública deste evento.",
    )
    extra_info = models.TextField(
        "Informações adicionais", blank=True,
        help_text="Ex.: o que levar, ponto de encontro, contato do responsável — aparece na página pública.",
    )

    status = models.CharField(
        "Status", max_length=10, choices=EventStatus.choices, default=EventStatus.DRAFT
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events_created",
        verbose_name="Criado por",
    )
    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Evento"
        verbose_name_plural = "Eventos"
        ordering = ["-start_datetime"]
        constraints = [
            models.UniqueConstraint(fields=["church", "slug"], name="unique_event_slug_per_church"),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)
            slug = base
            suffix = 1
            while Event.objects.filter(church=self.church, slug=slug).exclude(pk=self.pk).exists():
                suffix += 1
                slug = f"{base}-{suffix}"
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("events_public:detail", args=[self.church.slug, self.slug])

    @property
    def is_full(self):
        if self.capacity is None:
            return False
        return self._confirmed_registrations().count() >= self.capacity

    @property
    def spots_left(self):
        if self.capacity is None:
            return None
        return max(self.capacity - self._confirmed_registrations().count(), 0)

    def _confirmed_registrations(self):
        """Quem realmente ocupa uma vaga — cancelados e quem está na lista
        de espera não contam contra a capacidade."""
        return self.registrations.exclude(
            payment_status=Registration.PaymentStatus.CANCELLED
        ).exclude(on_waitlist=True)


class Registration(TenantModel):
    """Inscrição de uma pessoa em um evento. Aceita tanto membros/visitantes
    já cadastrados (`person`) quanto inscrições públicas avulsas, guardando
    nome/telefone/e-mail direto no registro para não exigir login."""

    class PaymentStatus(models.TextChoices):
        FREE = "FREE", "Gratuito"
        PENDING = "PENDING", "Aguardando pagamento"
        PAID = "PAID", "Pago"
        CANCELLED = "CANCELLED", "Cancelado"

    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name="registrations", verbose_name="Evento"
    )
    person = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="event_registrations",
        verbose_name="Pessoa",
    )

    # Preenchido sempre, mesmo quando `person` existe, para manter um
    # snapshot dos dados de contato no momento da inscrição.
    full_name = models.CharField("Nome completo", max_length=200)
    phone = models.CharField("Telefone (WhatsApp)", max_length=20, blank=True)
    email = models.EmailField("E-mail", blank=True)

    payment_status = models.CharField(
        "Status de pagamento", max_length=10,
        choices=PaymentStatus.choices, default=PaymentStatus.FREE,
    )
    amount_paid = models.DecimalField("Valor pago (R$)", max_digits=8, decimal_places=2, default=0)
    payment_reference = models.CharField(
        "Referência de pagamento", max_length=100, blank=True,
        help_text="ID da transação/PIX no gateway (Stripe/MercadoPago).",
    )

    on_waitlist = models.BooleanField(
        "Na lista de espera", default=False,
        help_text="Marcado automaticamente quando o evento já estava lotado no momento da inscrição.",
    )
    checkin_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    checked_in_at = models.DateTimeField("Check-in em", null=True, blank=True)

    registered_at = models.DateTimeField("Inscrito em", auto_now_add=True)
    privacy_consent_at = models.DateTimeField("Consentimento LGPD em", null=True, blank=True)

    class Meta:
        verbose_name = "Inscrição"
        verbose_name_plural = "Inscrições"
        ordering = ["-registered_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "person"],
                condition=models.Q(person__isnull=False),
                name="unique_person_per_event",
            )
        ]

    def __str__(self):
        return f"{self.full_name} → {self.event.title}"
