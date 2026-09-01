import secrets
import uuid

from django.conf import settings
from django.db import models

from core.tenancy import TenantModel


class SalaInfantil(TenantModel):
    """Uma "turma"/sala do ministério infantil (ex.: "Berçário 0-2",
    "Kids 6-9") — não amarrada a uma data específica, é a estrutura fixa
    que o check-in usa pra sugerir onde encaixar cada criança pela idade."""

    name = models.CharField("Nome", max_length=100)
    idade_min = models.PositiveIntegerField("Idade mínima", null=True, blank=True)
    idade_max = models.PositiveIntegerField("Idade máxima", null=True, blank=True)
    capacidade = models.PositiveIntegerField("Capacidade", null=True, blank=True)
    is_active = models.BooleanField("Ativa", default=True)

    class Meta:
        verbose_name = "Sala infantil"
        verbose_name_plural = "Salas infantis"
        ordering = ["idade_min", "name"]

    def __str__(self):
        return self.name

    def combina_com_idade(self, idade):
        if idade is None:
            return False
        if self.idade_min is not None and idade < self.idade_min:
            return False
        if self.idade_max is not None and idade > self.idade_max:
            return False
        return True


def _gerar_pickup_code():
    return "".join(secrets.choice("0123456789") for _ in range(4))


class Checkin(TenantModel):
    """Um check-in de criança — do check-in até o check-out (retirada
    pelo responsável). `child`/`sala` são FKs opcionais com snapshot em
    texto ao lado (`child_name`) — mesmo espírito de `events.Registration`,
    que aceita tanto pessoa cadastrada quanto entrada avulsa, e sobrevive
    se o cadastro for excluído depois (LGPD)."""

    child = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="checkins", verbose_name="Criança",
    )
    child_name = models.CharField("Nome da criança", max_length=200)
    sala = models.ForeignKey(
        SalaInfantil, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="checkins", verbose_name="Sala",
    )
    guardian_name = models.CharField("Nome do responsável", max_length=200)
    guardian_phone = models.CharField("Telefone do responsável", max_length=20, blank=True)

    pickup_code = models.CharField("Código de retirada", max_length=4, editable=False, blank=True)
    checkin_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    checked_in_at = models.DateTimeField("Check-in em", auto_now_add=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="checkins_feitos", verbose_name="Check-in feito por",
    )
    checked_out_at = models.DateTimeField("Check-out em", null=True, blank=True)
    checked_out_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="checkouts_feitos", verbose_name="Check-out feito por",
    )

    class Meta:
        verbose_name = "Check-in"
        verbose_name_plural = "Check-ins"
        ordering = ["-checked_in_at"]

    def __str__(self):
        return f"{self.child_name} — {self.checked_in_at:%d/%m %H:%M}"

    @property
    def is_active(self):
        return self.checked_out_at is None

    def save(self, *args, **kwargs):
        if not self.pickup_code:
            # Tenta achar um código sem outro check-in ATIVO igual na mesma
            # igreja (evita confundir dois responsáveis com o mesmo código
            # no mesmo dia) — 20 tentativas é sobra pra um espaço de 10.000
            # combinações contra uma lista ativa que nunca chega perto disso.
            for _ in range(20):
                code = _gerar_pickup_code()
                if not Checkin.todas_as_igrejas.filter(
                    church=self.church, pickup_code=code, checked_out_at__isnull=True
                ).exists():
                    self.pickup_code = code
                    break
            else:
                self.pickup_code = _gerar_pickup_code()
        super().save(*args, **kwargs)
