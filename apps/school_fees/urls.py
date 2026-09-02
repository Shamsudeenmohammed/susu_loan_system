from django.urls import path
from django.contrib.auth.decorators import login_required

from . import views

urlpatterns = [
    # Dashboard
    path('', views.dashboard, name='school_fees_dashboard'),

    # Students
    path('students/', views.student_list, name='school_fees_student_list'),
    path('students/new/', views.student_create, name='school_fees_student_create'),
    path('students/<int:pk>/', views.student_detail, name='school_fees_student_detail'),
    path('students/<int:pk>/edit/', views.student_update, name='school_fees_student_update'),
    path('students/<int:pk>/delete/', views.student_delete, name='school_fees_student_delete'),

    # Setups: classes
    path('classes/', views.class_list, name='school_fees_class_list'),
    path('classes/new/', views.class_create, name='school_fees_class_create'),
    path('classes/<int:pk>/edit/', views.class_update, name='school_fees_class_update'),

    # Setups: academic years
    path('years/', views.academic_year_list, name='school_fees_academic_year_list'),
    path('years/new/', views.academic_year_create, name='school_fees_academic_year_create'),
    path('years/<int:pk>/edit/', views.academic_year_update, name='school_fees_academic_year_update'),

    # Setups: terms
    path('terms/', views.term_list, name='school_fees_term_list'),
    path('terms/new/', views.term_create, name='school_fees_term_create'),
    path('terms/<int:pk>/edit/', views.term_update, name='school_fees_term_update'),

    # Setups: fee categories
    path('categories/', views.fee_category_list, name='school_fees_fee_category_list'),
    path('categories/new/', views.fee_category_create, name='school_fees_fee_category_create'),

    # Setups: fee structures
    path('fee-structures/', views.fee_structure_list, name='school_fees_fee_structure_list'),
    path('fee-structures/new/', views.fee_structure_create, name='school_fees_fee_structure_create'),
    path('fee-structures/<int:pk>/edit/', views.fee_structure_update, name='school_fees_fee_structure_update'),
    path('fee-structures/<int:pk>/delete/', views.fee_structure_delete, name='school_fees_fee_structure_delete'),

    # Fee accounts
    path('accounts/', views.fee_account_list, name='school_fees_fee_account_list'),
    path('accounts/<int:pk>/', views.fee_account_detail, name='school_fees_fee_account_detail'),
    path('accounts/<int:pk>/edit/', views.fee_account_update, name='school_fees_fee_account_update'),
    path('accounts/<int:pk>/pay/', views.payment_create, name='school_fees_payment_create'),
    path('accounts/<int:pk>/pay-online/', views.pay_online, name='school_fees_pay_online'),

    # Payments
    path('payments/', views.payment_list, name='school_fees_payment_list'),
    path('payments/new/', views.payment_create, name='school_fees_payment_new'),
    path('payments/<int:pk>/', views.payment_detail, name='school_fees_payment_detail'),
    path('payment/callback/', views.payment_callback, name='school_fees_payment_callback'),

    # Receipts
    path('receipts/<int:pk>/', views.receipt, name='school_fees_receipt'),
    path('receipts/', views.payment_list, name='school_fees_receipts'),

    # Outstanding fees
    path('outstanding/', views.outstanding_fees, name='school_fees_outstanding'),

    # Reminders
    path('reminders/templates/', views.reminder_template_list, name='school_fees_reminder_template_list'),
    path('reminders/templates/<int:pk>/edit/', views.reminder_template_edit, name='school_fees_reminder_template_edit'),
    path('reminders/send/', views.send_reminders, name='school_fees_send_reminders'),
    path('reminders/send/class/', views.send_reminders_by_class, name='school_fees_send_reminders_class'),
    path('reminders/send/overdue/', views.send_overdue_reminders, name='school_fees_send_overdue_reminders'),
    path('reminders/log/', views.reminder_log, name='school_fees_reminder_log'),

    # Reports
    path('reports/', views.reports, name='school_fees_reports'),
    path('reports/daily/', views.report_daily_payments, name='school_fees_report_daily'),
    path('reports/monthly/', views.report_monthly_payments, name='school_fees_report_monthly'),
    path('reports/year/', views.report_year_collections, name='school_fees_report_year'),
    path('reports/outstanding/', views.report_outstanding, name='school_fees_report_outstanding'),
    path('reports/overdue/', views.report_overdue, name='school_fees_report_overdue'),
    path('reports/status/', views.report_status, name='school_fees_report_status'),
    path('reports/classes/', views.report_class_collections, name='school_fees_report_class'),
    path('reports/export/', views.export_fees_csv, name='school_fees_export_csv'),
]
