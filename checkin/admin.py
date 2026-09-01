from django.contrib import admin

from checkin.models import Checkin, SalaInfantil


@admin.register(SalaInfantil)
class SalaInfantilAdmin(admin.ModelAdmin):
    list_display = ("name", "idade_min", "idade_max", "capacidade", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Checkin)
class CheckinAdmin(admin.ModelAdmin):
    list_display = ("child_name", "sala", "guardian_name", "pickup_code", "checked_in_at", "checked_out_at")
    list_filter = ("sala",)
    search_fields = ("child_name", "guardian_name", "pickup_code")
    date_hierarchy = "checked_in_at"
