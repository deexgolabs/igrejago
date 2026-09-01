from django.contrib import admin

from sermons.models import Sermon


@admin.register(Sermon)
class SermonAdmin(admin.ModelAdmin):
    list_display = ("title", "church", "preacher_name", "date", "series", "is_published")
    list_filter = ("is_published", "series")
    search_fields = ("title", "preacher_name")
    date_hierarchy = "date"
