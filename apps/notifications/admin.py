from django.contrib import admin
from .models import SMSNotification


@admin.register(SMSNotification)
class SMSNotificationAdmin(admin.ModelAdmin):
    list_display = ['notification_number', 'phone_number', 'notification_type', 'status', 'created_at']
    list_filter = ['status', 'notification_type', 'created_at']
    search_fields = ['notification_number', 'phone_number', 'brevo_message_id']
    readonly_fields = ['notification_number', 'created_at']
