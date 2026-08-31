from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Igreja", {"fields": ("church", "role", "person")}),
    )
    list_display = ("username", "get_full_name", "church", "role", "is_staff")
    list_filter = ("church", "role", "is_staff", "is_active")
