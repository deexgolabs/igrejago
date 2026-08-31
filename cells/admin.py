from django.contrib import admin

from cells.models import Cell, CellMeeting


class CellMeetingInline(admin.TabularInline):
    model = CellMeeting
    extra = 0
    fields = ("date", "visitors_count", "notes")


@admin.register(Cell)
class CellAdmin(admin.ModelAdmin):
    list_display = ("name", "leader", "meeting_weekday", "meeting_time", "is_active")
    list_filter = ("is_active", "meeting_weekday")
    search_fields = ("name",)
    inlines = [CellMeetingInline]


@admin.register(CellMeeting)
class CellMeetingAdmin(admin.ModelAdmin):
    list_display = ("cell", "date", "total_present", "visitors_count")
    list_filter = ("cell",)
    date_hierarchy = "date"
