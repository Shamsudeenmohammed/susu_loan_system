from django.urls import path
from . import views
from apps.customers.views import susu_account_activate

urlpatterns = [
    path('', views.susu_account_list, name='susu_account_list'),
    path('create/', views.susu_account_create, name='susu_account_create'),
    path('<int:pk>/', views.susu_account_detail, name='susu_account_detail'),
    path('<int:pk>/edit/', views.susu_account_update, name='susu_account_update'),
    path('<int:pk>/activate/', susu_account_activate, name='susu_account_activate'),
]
