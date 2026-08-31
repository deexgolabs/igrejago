from django.contrib import admin

from events.models import Event, Registration


class RegistrationInline(admin.TabularInline):
    model = Registration
    extra = 0
    fields = ("full_name", "phone", "email", "payment_status", "amount_paid")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "start_datetime", "is_paid", "price", "status", "spots_left")
    list_filter = ("status", "is_paid")
    search_fields = ("title",)
    prepopulated_fields = {"slug": ("title",)}
    inlines = [RegistrationInline]


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("full_name", "event", "payment_status", "amount_paid", "registered_at")
    list_filter = ("payment_status", "event")
    search_fields = ("full_name", "phone", "email")
