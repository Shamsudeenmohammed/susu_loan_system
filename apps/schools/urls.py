from django.urls import path

from . import views

urlpatterns = [
    path('switch/', views.switch_school, name='switch_school'),
    path('staff/', views.staff_list, name='school_staff_list'),
]
