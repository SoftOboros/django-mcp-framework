"""Test admins exercising INV-DMCP01-1/2/3/5: search_fields, action with
permissions, custom get_fields hiding `secret_note` for non-superusers, and an
inline that MUST NOT cause a separate tool to surface.
"""

from django.contrib import admin

from .models import Post, Tag


@admin.action(description="Publish the selected posts", permissions=["change"])
def publish(modeladmin, request, queryset):
    queryset.update(published=True)


class TagInline(admin.TabularInline):
    model = Post.tags.through
    extra = 0


class PostAdmin(admin.ModelAdmin):
    search_fields = ("title",)
    actions = (publish,)
    inlines = (TagInline,)

    def get_fields(self, request, obj=None):
        # INV-DMCP01-3 visible-field parity: hide `secret_note` from non-
        # superusers; the MCP-derived schema should track this set.
        fields = [
            "title",
            "body",
            "author",
            "tags",
            "status",
            "visibility",
            "published",
            "attachment",
        ]
        if getattr(request, "user", None) is not None and request.user.is_superuser:
            fields.append("secret_note")
        return fields


admin.site.register(Post, PostAdmin)
admin.site.register(Tag)
