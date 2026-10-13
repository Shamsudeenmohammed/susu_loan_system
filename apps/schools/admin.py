from django.contrib import admin

from .models import School


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'short_name', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'code', 'short_name', 'location']
