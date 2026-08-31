from django.contrib import admin
from .models import SMSNotification


@admin.register(SMSNotification)
class SMSNotificationAdmin(admin.ModelAdmin):
    list_display = ['notification_number', 'phone_number', 'notification_type', 'status', 'provider', 'created_at']
    list_filter = ['status', 'notification_type', 'provider', 'created_at']
    search_fields = ['notification_number', 'phone_number', 'provider_message_id']
    readonly_fields = ['notification_number', 'created_at', 'unique_key']
