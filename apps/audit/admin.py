from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'action', 'description', 'object_type', 'object_id', 'created_at']
    list_filter = ['action', 'created_at']
    search_fields = ['description', 'user__email']
    readonly_fields = ['created_at']
