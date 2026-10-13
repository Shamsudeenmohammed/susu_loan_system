from django.urls import path

from . import views

app_name = 'academics'

urlpatterns = [
    path('', views.home, name='home'),
    path('teacher/', views.teacher_dashboard, name='teacher_dashboard'),
    path('dashboard/', views.head_dashboard, name='head_dashboard'),

    path('subjects/', views.subject_list, name='subject_list'),
    path('subjects/new/', views.subject_form, name='subject_create'),
    path('subjects/<int:pk>/edit/', views.subject_form, name='subject_edit'),

    path('class-subjects/', views.class_subject_list, name='class_subject_list'),
    path('class-subjects/new/', views.class_subject_form, name='class_subject_create'),
    path('class-subjects/<int:pk>/edit/', views.class_subject_form, name='class_subject_edit'),

    path('teacher-assignments/', views.teacher_assignment_list, name='teacher_assignment_list'),
    path('teacher-assignments/new/', views.teacher_assignment_form, name='teacher_assignment_create'),
    path('teacher-assignments/<int:pk>/edit/', views.teacher_assignment_form, name='teacher_assignment_edit'),

    path('grading-scales/', views.grading_scale_list, name='grading_scale_list'),
    path('grading-scales/new/', views.grading_scale_form, name='grading_scale_create'),
    path('grading-scales/<int:pk>/edit/', views.grading_scale_form, name='grading_scale_edit'),

    path('settings/', views.academic_settings, name='academic_settings'),
    path('report-fields/', views.report_fields, name='report_fields'),

    path('assessments/', views.assessment_list, name='assessment_list'),
    path('assessments/new/', views.assessment_form, name='assessment_create'),
    path('assessments/<int:pk>/edit/', views.assessment_form, name='assessment_edit'),
    path('assessments/<int:pk>/delete/', views.assessment_delete, name='assessment_delete'),
    path('assessments/<int:pk>/marks/', views.mark_entry, name='mark_entry'),

    path('submissions/', views.submissions, name='submissions'),
    path('submissions/<int:pk>/review/', views.submission_review, name='submission_review'),

    path('requests/', views.update_request_list, name='update_request_list'),
    path('requests/new/', views.update_request_create, name='update_request_create'),
    path('requests/<int:pk>/', views.update_request_detail, name='update_request_detail'),

    path('academic-year/override/', views.academic_year_override,
         name='academic_year_override'),

    path('attendance/', views.attendance_index, name='attendance_index'),
    path('attendance/take/', views.attendance_take, name='attendance_take'),
    path('attendance/report/', views.attendance_report, name='attendance_report'),

    path('results/', views.results, name='results'),
    path('results/promote/<int:student_id>/', views.promote, name='promote'),

    path('reports/terminal/<int:student_id>/', views.terminal_report, name='terminal_report'),
    path('reports/terminal/pdf/', views.terminal_report_pdf, name='terminal_report_pdf'),

    path('calendar/', views.calendar, name='calendar'),
    path('calendar/new/', views.calendar_event_form, name='calendar_create'),
    path('calendar/<int:pk>/edit/', views.calendar_event_form, name='calendar_edit'),
    path('calendar/<int:pk>/delete/', views.calendar_event_delete, name='calendar_delete'),

    path('announcements/', views.announcements, name='announcements'),
    path('announcements/new/', views.announcement_form, name='announcement_create'),
    path('announcements/<int:pk>/edit/', views.announcement_form, name='announcement_edit'),
]
