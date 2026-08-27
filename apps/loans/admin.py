from django.contrib import admin
from .models import LoanProduct, Loan, RepaymentSchedule, LoanRepayment, LoanPolicy, EligibilityAudit


@admin.register(LoanProduct)
class LoanProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'interest_rate', 'interest_method', 'is_active']
    list_filter = ['is_active', 'interest_method']
    search_fields = ['name', 'code']


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ['loan_number', 'customer', 'loan_product', 'principal_amount', 'status', 'created_at']
    list_filter = ['status', 'loan_product']
    search_fields = ['loan_number', 'customer__first_name', 'customer__last_name']
    readonly_fields = ['loan_number', 'created_at']


@admin.register(RepaymentSchedule)
class RepaymentScheduleAdmin(admin.ModelAdmin):
    list_display = ['loan', 'installment_number', 'due_date', 'total_due', 'amount_paid', 'status']
    list_filter = ['status']


@admin.register(LoanRepayment)
class LoanRepaymentAdmin(admin.ModelAdmin):
    list_display = ['repayment_number', 'loan', 'amount', 'payment_method', 'created_at']
    list_filter = ['payment_method', 'created_at']


@admin.register(LoanPolicy)
class LoanPolicyAdmin(admin.ModelAdmin):
    list_display = ['name', 'minimum_membership_days', 'minimum_savings', 'maximum_loan_multiplier', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name']


@admin.register(EligibilityAudit)
class EligibilityAuditAdmin(admin.ModelAdmin):
    list_display = ['customer', 'eligible', 'eligibility_score', 'created_at']
    list_filter = ['eligible']
    search_fields = ['customer__first_name', 'customer__last_name', 'customer__customer_number']
    readonly_fields = ['customer', 'eligible', 'eligibility_score', 'maximum_loan_amount',
                       'passed_criteria', 'failed_criteria', 'snapshot_json', 'created_at']
