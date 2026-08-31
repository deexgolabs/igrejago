from django.contrib import admin

from linkbio.models import BioPage, Link


class LinkInline(admin.TabularInline):
    model = Link
    extra = 1
    fields = ("title", "url", "link_type", "icon", "order", "is_active")


@admin.register(BioPage)
class BioPageAdmin(admin.ModelAdmin):
    list_display = ("church_name", "slug", "is_active")
    inlines = [LinkInline]


@admin.register(Link)
class LinkAdmin(admin.ModelAdmin):
    list_display = ("title", "page", "link_type", "order", "is_active", "click_count")
    list_filter = ("link_type", "is_active", "page")
