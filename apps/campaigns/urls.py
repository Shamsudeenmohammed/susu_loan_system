from django.urls import path

from . import views

urlpatterns = [
    path('', views.campaign_dashboard, name='campaign_dashboard'),
    path('create/', views.campaign_create, name='campaign_create'),
    path('history/', views.campaign_history, name='campaign_history'),
    path('logs/', views.campaign_logs, name='campaign_logs'),
    path('templates/', views.template_list, name='template_list'),
    path('templates/create/', views.template_create, name='template_create'),
    path('templates/<int:pk>/edit/', views.template_edit, name='template_edit'),
    path('templates/<int:pk>/delete/', views.template_delete, name='template_delete'),
    path('<int:pk>/', views.campaign_detail, name='campaign_detail'),
    path('<int:pk>/test/', views.campaign_send_test, name='campaign_send_test'),
    path('<int:pk>/retry/', views.campaign_retry, name='campaign_retry'),
    path('<int:pk>/cancel/', views.campaign_cancel, name='campaign_cancel'),
    path('<int:pk>/export/', views.campaign_export, name='campaign_export'),
]
