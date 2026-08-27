from django.urls import path
from . import views

urlpatterns = [
    path('', views.report_index, name='report_index'),
    path('customers/', views.customer_report, name='customer_report'),
    path('contributions/', views.contributions_report, name='contributions_report'),
    path('loans/', views.loan_report, name='loan_report'),
    path('repayments/', views.repayments_report, name='repayments_report'),
    path('overdue/', views.overdue_report, name='overdue_report'),
    path('daily/', views.daily_summary, name='daily_summary'),
    path('export/', views.export_csv, name='export_csv'),
]
