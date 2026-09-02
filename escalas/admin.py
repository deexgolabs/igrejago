from django.contrib import admin

from escalas.models import Escala, EscalaVoluntario, IndisponibilidadeVoluntario, TrocaEscala


class EscalaVoluntarioInline(admin.TabularInline):
    model = EscalaVoluntario
    extra = 0
    fields = ("person", "status", "confirmed_at")
    readonly_fields = ("confirmed_at",)


@admin.register(Escala)
class EscalaAdmin(admin.ModelAdmin):
    list_display = ("department", "date", "time", "title")
    list_filter = ("department",)
    date_hierarchy = "date"
    inlines = [EscalaVoluntarioInline]


@admin.register(EscalaVoluntario)
class EscalaVoluntarioAdmin(admin.ModelAdmin):
    list_display = ("person", "escala", "status", "confirmed_at")
    list_filter = ("status",)


@admin.register(IndisponibilidadeVoluntario)
class IndisponibilidadeVoluntarioAdmin(admin.ModelAdmin):
    list_display = ("person", "date", "motivo")
    date_hierarchy = "date"


@admin.register(TrocaEscala)
class TrocaEscalaAdmin(admin.ModelAdmin):
    list_display = ("escala_voluntario", "status", "aceito_por", "created_at")
    list_filter = ("status",)
