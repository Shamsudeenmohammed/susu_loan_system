from django.contrib import admin
from .models import SusuAccount


@admin.register(SusuAccount)
class SusuAccountAdmin(admin.ModelAdmin):
    list_display = ['account_number', 'customer', 'contribution_frequency', 'current_balance', 'status']
    list_filter = ['status', 'contribution_frequency']
    search_fields = ['account_number', 'customer__first_name', 'customer__last_name']
    readonly_fields = ['account_number', 'current_balance']
