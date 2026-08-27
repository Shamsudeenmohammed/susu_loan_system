from django.urls import path
from . import views

urlpatterns = [
    path('products/', views.loan_product_list, name='loan_product_list'),
    path('products/create/', views.loan_product_create, name='loan_product_create'),
    path('products/<int:pk>/edit/', views.loan_product_update, name='loan_product_update'),
    path('', views.loan_list, name='loan_list'),
    path('apply/', views.loan_apply, name='loan_apply'),
    path('eligibility/', views.loan_eligibility, name='loan_eligibility'),
    path('eligibility/audit/', views.eligibility_audit_list, name='eligibility_audit_list'),
    path('policy/', views.loan_policy_list, name='loan_policy_list'),
    path('policy/create/', views.loan_policy_create, name='loan_policy_create'),
    path('policy/<int:pk>/edit/', views.loan_policy_update, name='loan_policy_update'),
    path('<int:pk>/', views.loan_detail, name='loan_detail'),
    path('<int:pk>/review/', views.loan_review, name='loan_review'),
    path('<int:pk>/disburse/', views.loan_disburse, name='loan_disburse'),
    path('repayments/', views.loan_repayment_list, name='loan_repayment_list'),
    path('repayments/record/', views.record_repayment, name='record_repayment'),
    path('overdue/', views.overdue_loans, name='overdue_loans'),
]
