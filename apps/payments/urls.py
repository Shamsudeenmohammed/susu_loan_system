from django.urls import path
from . import views

urlpatterns = [
    path('transactions/', views.transaction_list, name='transaction_list'),
    path('transactions/<int:pk>/', views.transaction_detail, name='transaction_detail'),
    path('contribute/', views.record_contribution_view, name='record_contribution'),
    path('withdrawals/', views.withdrawal_list, name='withdrawal_list'),
    path('withdrawals/request/', views.withdrawal_request_view, name='withdrawal_request'),
    path('withdrawals/<int:pk>/review/', views.withdrawal_review, name='withdrawal_review'),
    path('my-transactions/', views.customer_transactions, name='customer_transactions'),
    path('my-contribute/', views.customer_contribute, name='customer_contribute'),
    path('contribute/callback/', views.customer_contribute_callback, name='customer_contribute_callback'),
    path('check/', views.payment_check, name='payment_check'),
    path('webhook/paystack/', views.paystack_webhook, name='paystack_webhook'),
]
