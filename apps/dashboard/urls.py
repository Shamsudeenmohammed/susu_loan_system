from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('customer/', views.dashboard_view, name='customer_dashboard'),
]
