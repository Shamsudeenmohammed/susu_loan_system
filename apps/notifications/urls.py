from django.urls import path
from . import views

urlpatterns = [
    path('sms/', views.sms_notification_list, name='sms_notification_list'),
    path('sms/<int:pk>/', views.sms_notification_detail, name='sms_notification_detail'),
    path('sms/<int:pk>/retry/', views.sms_retry, name='sms_retry'),
]
