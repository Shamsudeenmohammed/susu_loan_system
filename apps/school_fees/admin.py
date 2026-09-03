from django.contrib import admin
from .models import (
    SchoolClass,
    AcademicYear,
    Term,
    Student,
    FeeCategory,
    FeeStructure,
    StudentFeeAccount,
    FeePayment,
    ReminderTemplate,
    ReminderLog,
)


@admin.register(SchoolClass)
class SchoolClassAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'code']


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ['name', 'start_date', 'end_date', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name']


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ['academic_year', 'name', 'term_number', 'start_date', 'end_date']
    list_filter = ['academic_year', 'term_number']
    search_fields = ['name', 'academic_year__name']


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ['student_id', 'first_name', 'last_name', 'school_class', 'is_active', 'created_at']
    list_filter = ['school_class', 'is_active']
    search_fields = ['student_id', 'first_name', 'last_name', 'parent_name', 'parent_phone']
    readonly_fields = ['student_id', 'created_at', 'updated_at']
    autocomplete_fields = ['school_class']


@admin.register(FeeCategory)
class FeeCategoryAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'code']


@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = ['academic_year', 'term', 'school_class', 'fee_category', 'amount', 'due_date', 'is_active']
    list_filter = ['academic_year', 'term', 'school_class', 'fee_category', 'is_active']
    search_fields = ['academic_year__name', 'school_class__name', 'fee_category__name', 'description']
    autocomplete_fields = ['academic_year', 'term', 'school_class', 'fee_category']


@admin.register(StudentFeeAccount)
class StudentFeeAccountAdmin(admin.ModelAdmin):
    list_display = [
        'account_number', 'student', 'academic_year', 'term', 'total_fees',
        'amount_paid', 'outstanding_balance', 'status', 'last_payment_date',
    ]
    list_filter = ['academic_year', 'term', 'status']
    search_fields = ['account_number', 'student__student_id', 'student__first_name', 'student__last_name']
    readonly_fields = ['account_number', 'outstanding_balance', 'created_at', 'updated_at']
    autocomplete_fields = ['student', 'academic_year', 'term']


@admin.register(FeePayment)
class FeePaymentAdmin(admin.ModelAdmin):
    list_display = [
        'receipt_number', 'student', 'account', 'amount', 'payment_date',
        'payment_method', 'reference', 'is_online', 'recorded_by', 'created_at',
    ]
    list_filter = ['payment_method', 'payment_date', 'academic_year', 'term', 'is_online']
    search_fields = ['receipt_number', 'reference', 'student__first_name', 'student__last_name']
    readonly_fields = ['receipt_number', 'previous_balance', 'remaining_balance', 'created_at']
    autocomplete_fields = ['student', 'account', 'academic_year', 'term', 'recorded_by']


@admin.register(ReminderTemplate)
class ReminderTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'reminder_type', 'is_active', 'updated_at']
    list_filter = ['reminder_type', 'is_active']
    search_fields = ['name', 'message']


@admin.register(ReminderLog)
class ReminderLogAdmin(admin.ModelAdmin):
    list_display = ['student', 'parent_phone', 'reminder_type', 'status', 'created_at']
    list_filter = ['status', 'reminder_type', 'created_at']
    search_fields = ['student__first_name', 'student__last_name', 'parent_phone', 'message']
    readonly_fields = ['student', 'parent_phone', 'message', 'status', 'unique_key', 'created_at']
