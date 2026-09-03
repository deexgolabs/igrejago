from django.contrib import admin

from assistant.models import Conversation, ConversationMessage, PersonDraft, PersonUpdateLink


class ConversationMessageInline(admin.TabularInline):
    model = ConversationMessage
    extra = 0
    readonly_fields = ("direction", "body", "created_at")
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("phone", "church", "state", "person", "last_message_at")
    list_filter = ("state",)
    search_fields = ("phone",)
    inlines = [ConversationMessageInline]


@admin.register(PersonDraft)
class PersonDraftAdmin(admin.ModelAdmin):
    list_display = ("__str__", "church", "origin", "status", "requested_at", "processed_by")
    list_filter = ("status", "origin")


@admin.register(PersonUpdateLink)
class PersonUpdateLinkAdmin(admin.ModelAdmin):
    list_display = ("person", "church", "created_at", "last_used_at")
    readonly_fields = ("token",)
