from django.contrib import admin

from people.models import Department, Family, Person, Tag


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "leader")
    search_fields = ("name",)


@admin.register(Family)
class FamilyAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "color")
    search_fields = ("name",)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("full_name", "role", "status", "pipeline_stage", "department", "is_member", "is_visitor", "phone")
    list_filter = ("role", "status", "pipeline_stage", "department", "is_member", "is_visitor")
    search_fields = ("full_name", "phone", "email")
    date_hierarchy = "created_at"
