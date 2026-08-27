from django.contrib import admin
from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['customer_number', 'first_name', 'last_name', 'phone', 'status', 'created_at']
    list_filter = ['status', 'gender', 'created_at']
    search_fields = ['customer_number', 'first_name', 'last_name', 'phone', 'email']
    readonly_fields = ['customer_number', 'created_at', 'updated_at']
