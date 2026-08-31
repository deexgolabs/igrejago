from django.db import models
from django.urls import reverse

from core.tenancy import TenantModel


class Cell(TenantModel):
    """Um pequeno grupo/célula."""

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Segunda-feira"
        TUESDAY = 1, "Terça-feira"
        WEDNESDAY = 2, "Quarta-feira"
        THURSDAY = 3, "Quinta-feira"
        FRIDAY = 4, "Sexta-feira"
        SATURDAY = 5, "Sábado"
        SUNDAY = 6, "Domingo"

    name = models.CharField("Nome", max_length=100)
    leader = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_cells",
        verbose_name="Líder",
    )
    members = models.ManyToManyField(
        "people.Person", related_name="cells", blank=True, verbose_name="Membros"
    )
    meeting_weekday = models.IntegerField(
        "Dia da semana", choices=Weekday.choices, null=True, blank=True
    )
    meeting_time = models.TimeField("Horário", null=True, blank=True)
    address = models.CharField("Endereço", max_length=255, blank=True)
    is_active = models.BooleanField("Ativa", default=True)

    class Meta:
        verbose_name = "Célula"
        verbose_name_plural = "Células"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("cells:detail", args=[self.pk])


class CellMeeting(TenantModel):
    """Relatório semanal de presença de uma célula."""

    cell = models.ForeignKey(Cell, on_delete=models.CASCADE, related_name="meetings", verbose_name="Célula")
    date = models.DateField("Data")
    attendees = models.ManyToManyField(
        "people.Person", related_name="cell_meetings_attended", blank=True, verbose_name="Presentes"
    )
    visitors_count = models.PositiveIntegerField("Visitantes (sem cadastro)", default=0)
    notes = models.TextField("Observações", blank=True)
    created_at = models.DateTimeField("Registrado em", auto_now_add=True)

    class Meta:
        verbose_name = "Reunião"
        verbose_name_plural = "Reuniões"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.cell.name} — {self.date}"

    @property
    def total_present(self):
        return self.attendees.count() + self.visitors_count
