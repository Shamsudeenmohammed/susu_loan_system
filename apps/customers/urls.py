from django.urls import path
from . import views

urlpatterns = [
    path('', views.customer_list, name='customer_list'),
    path('create/', views.CustomerCreateView.as_view(), name='customer_create'),
    path('pending-approvals/', views.pending_customers, name='customer_pending_approvals'),
    path('<int:pk>/', views.customer_detail, name='customer_detail'),
    path('<int:pk>/edit/', views.CustomerUpdateView.as_view(), name='customer_update'),
    path('<int:pk>/approve/', views.customer_approve, name='customer_approve'),
    path('<int:pk>/reject/', views.customer_reject, name='customer_reject'),
]
