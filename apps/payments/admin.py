from django.contrib import admin
from .models import Transaction, Withdrawal


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ['transaction_number', 'customer', 'transaction_type', 'amount', 'balance_after', 'created_at']
    list_filter = ['transaction_type', 'payment_method', 'created_at']
    search_fields = ['transaction_number', 'customer__first_name', 'customer__last_name']
    readonly_fields = ['transaction_number', 'created_at']


@admin.register(Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):
    list_display = ['withdrawal_number', 'customer', 'amount', 'status', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['withdrawal_number', 'customer__first_name']
    readonly_fields = ['withdrawal_number', 'created_at']
