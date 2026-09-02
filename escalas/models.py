import uuid

from django.db import models

from core.tenancy import TenantModel
from people.models import Department


class Escala(TenantModel):
    """Uma escala de serviço — um departamento/ministério, numa data
    específica (ex.: "Louvor, culto de domingo de manhã, 07/09")."""

    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="escalas", verbose_name="Ministério",
    )
    date = models.DateField("Data")
    time = models.TimeField("Horário", null=True, blank=True)
    title = models.CharField("Título", max_length=200, blank=True)
    created_at = models.DateTimeField("Criada em", auto_now_add=True)

    class Meta:
        verbose_name = "Escala"
        verbose_name_plural = "Escalas"
        ordering = ["date", "time"]

    def __str__(self):
        return f"{self.department.name} — {self.date:%d/%m/%Y}"


class EscalaVoluntario(TenantModel):
    """Um voluntário escalado numa `Escala` — status de confirmação
    próprio, com um link público (por token) pra confirmar ou recusar
    sem precisar de login (mesmo espírito de `Registration.checkin_token`)."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Aguardando confirmação"
        CONFIRMED = "CONFIRMED", "Confirmado"
        DECLINED = "DECLINED", "Não vai poder ir"

    escala = models.ForeignKey(Escala, on_delete=models.CASCADE, related_name="voluntarios", verbose_name="Escala")
    person = models.ForeignKey(
        "people.Person", on_delete=models.CASCADE, related_name="escalas_voluntario", verbose_name="Voluntário",
    )
    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.PENDING)
    confirm_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    confirmed_at = models.DateTimeField("Respondido em", null=True, blank=True)

    class Meta:
        verbose_name = "Voluntário escalado"
        verbose_name_plural = "Voluntários escalados"
        ordering = ["escala__date"]
        constraints = [
            models.UniqueConstraint(fields=["escala", "person"], name="unique_person_per_escala"),
        ]

    def __str__(self):
        return f"{self.person.full_name} — {self.escala}"


class IndisponibilidadeVoluntario(TenantModel):
    """Uma data em que a pessoa avisou que NÃO pode servir — usado por
    `gerar_escalas_mensais` pra pular ela no rodízio naquele domingo
    específico (ver o comando). Sem `department`: a indisponibilidade é
    da PESSOA numa data, independente de qual departamento ela serve."""

    person = models.ForeignKey(
        "people.Person", on_delete=models.CASCADE, related_name="indisponibilidades", verbose_name="Pessoa",
    )
    date = models.DateField("Data")
    motivo = models.CharField("Motivo", max_length=200, blank=True)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Indisponibilidade"
        verbose_name_plural = "Indisponibilidades"
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["person", "date"], name="unique_indisponibilidade_por_dia"),
        ]

    def __str__(self):
        return f"{self.person.full_name} — {self.date:%d/%m/%Y}"


class TrocaEscala(TenantModel):
    """Pedido de troca de escala, self-service — o voluntário confirmado
    pede pra repassar o compromisso, os colegas do mesmo departamento
    recebem o link público (`token`, mesmo espírito de
    `EscalaVoluntario.confirm_token`) e o primeiro que aceitar assume o
    lugar. A secretaria/líder só acompanha, não precisa agir."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Aguardando alguém aceitar"
        ACEITA = "ACEITA", "Aceita"
        CANCELADA = "CANCELADA", "Cancelada"

    escala_voluntario = models.ForeignKey(
        EscalaVoluntario, on_delete=models.CASCADE, related_name="trocas", verbose_name="Escalado",
    )
    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.PENDING)
    aceito_por = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="trocas_aceitas", verbose_name="Aceita por",
    )
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    resolved_at = models.DateTimeField("Resolvida em", null=True, blank=True)

    class Meta:
        verbose_name = "Troca de escala"
        verbose_name_plural = "Trocas de escala"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Troca — {self.escala_voluntario} ({self.get_status_display()})"
