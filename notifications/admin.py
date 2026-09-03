from django.contrib import admin

from notifications.models import MessageTemplate, WhatsAppMessage, WhatsAppMetaTemplate


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    list_display = (
        "phone", "person", "status", "delivery_status", "retry_count",
        "scheduled_for", "sent_at", "campaign_label",
    )
    list_filter = ("status", "delivery_status", "campaign_label")
    search_fields = ("phone", "person__full_name", "message")
    date_hierarchy = "created_at"
    readonly_fields = (
        "sent_at", "error_message", "created_by", "created_at",
        "external_id", "delivered_at", "read_at",
    )


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "created_at")
    search_fields = ("name", "body")


@admin.register(WhatsAppMetaTemplate)
class WhatsAppMetaTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "language", "status", "meta_template_id", "submitted_at")
    list_filter = ("status", "category")
    search_fields = ("name", "meta_template_id")
    readonly_fields = ("meta_template_id", "submitted_at", "status_checked_at", "created_at")
