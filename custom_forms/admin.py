from django.contrib import admin

from custom_forms.models import CustomForm, FormAnswer, FormField, FormResponse


class FormFieldInline(admin.TabularInline):
    model = FormField
    extra = 1


@admin.register(CustomForm)
class CustomFormAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "send_whatsapp_confirmation", "created_at")
    list_filter = ("is_active", "send_whatsapp_confirmation")
    search_fields = ("title",)
    inlines = [FormFieldInline]


class FormAnswerInline(admin.TabularInline):
    model = FormAnswer
    extra = 0
    readonly_fields = ("field", "value")
    can_delete = False


@admin.register(FormResponse)
class FormResponseAdmin(admin.ModelAdmin):
    list_display = ("form", "person", "submitted_at")
    list_filter = ("form",)
    date_hierarchy = "submitted_at"
    inlines = [FormAnswerInline]
