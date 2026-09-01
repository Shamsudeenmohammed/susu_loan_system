from django.contrib import admin

from .models import SMSCampaign, SMSMessageLog, SMSTemplate


class SMSMessageLogInline(admin.TabularInline):
    model = SMSMessageLog
    extra = 0
    readonly_fields = [
        'customer', 'phone_number', 'message', 'status', 'sms_units',
        'retry_count', 'created_at', 'sent_at',
    ]
    can_delete = False


@admin.register(SMSCampaign)
class SMSCampaignAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'campaign_type', 'target_group', 'status', 'trigger',
        'recipient_count', 'sent_count', 'failed_count', 'scheduled_at', 'created_at',
    ]
    list_filter = ['status', 'campaign_type', 'target_group', 'trigger']
    search_fields = ['name', 'uid']
    readonly_fields = [
        'uid', 'recipient_count', 'valid_phone_count', 'missing_phone_count',
        'excluded_count', 'sent_count', 'delivered_count', 'failed_count',
        'pending_count', 'sms_units', 'started_at', 'completed_at',
        'created_at', 'updated_at',
    ]
    inlines = [SMSMessageLogInline]
    list_select_related = ['created_by']


@admin.register(SMSMessageLog)
class SMSMessageLogAdmin(admin.ModelAdmin):
    list_display = ['campaign', 'phone_number', 'status', 'sms_units', 'sent_at', 'created_at']
    list_filter = ['status']
    search_fields = ['phone_number', 'unique_key']
    readonly_fields = ['message', 'unique_key', 'created_at', 'sent_at', 'delivered_at']


@admin.register(SMSTemplate)
class SMSTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'campaign_type', 'is_active', 'created_at']
    list_filter = ['campaign_type', 'is_active']
    search_fields = ['name']
