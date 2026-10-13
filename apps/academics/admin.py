from django.contrib import admin

from . import models


@admin.register(models.Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'school', 'is_core', 'is_active')
    list_filter = ('school', 'is_core', 'is_active')
    search_fields = ('name', 'code')


@admin.register(models.ClassSubject)
class ClassSubjectAdmin(admin.ModelAdmin):
    list_display = ('school_class', 'subject', 'school', 'is_active')
    list_filter = ('school', 'is_active')


@admin.register(models.TeacherAssignment)
class TeacherAssignmentAdmin(admin.ModelAdmin):
    list_display = ('teacher', 'school_class', 'subject', 'academic_year', 'term', 'is_active')
    list_filter = ('school', 'academic_year', 'is_active')


@admin.register(models.GradingScale)
class GradingScaleAdmin(admin.ModelAdmin):
    list_display = ('name', 'school', 'is_default', 'is_active')


@admin.register(models.GradeBand)
class GradeBandAdmin(admin.ModelAdmin):
    list_display = ('grade', 'scale', 'min_score', 'max_score', 'order')


@admin.register(models.AcademicSettings)
class AcademicSettingsAdmin(admin.ModelAdmin):
    list_display = ('school', 'pass_mark', 'currency', 'show_position')


@admin.register(models.Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'school_class', 'subject', 'term', 'max_score', 'weight', 'is_active')
    list_filter = ('school', 'academic_year', 'term', 'is_active')


@admin.register(models.StudentMark)
class StudentMarkAdmin(admin.ModelAdmin):
    list_display = ('student', 'assessment', 'score', 'status')
    list_filter = ('status',)


@admin.register(models.MarkSubmission)
class MarkSubmissionAdmin(admin.ModelAdmin):
    list_display = ('school_class', 'subject', 'term', 'status', 'submitted_by', 'reviewed_by')
    list_filter = ('school', 'status', 'term')


@admin.register(models.ResultPublication)
class ResultPublicationAdmin(admin.ModelAdmin):
    list_display = ('school_class', 'term', 'is_published', 'published_by', 'published_at')
    list_filter = ('school', 'is_published')


@admin.register(models.TerminalComment)
class TerminalCommentAdmin(admin.ModelAdmin):
    list_display = ('student', 'academic_year', 'term', 'promotion_status')


@admin.register(models.AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ('school_class', 'subject', 'date', 'session_label', 'taken_by')
    list_filter = ('school', 'date')


@admin.register(models.AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('student', 'session', 'status')
    list_filter = ('status',)


@admin.register(models.AttendanceCorrection)
class AttendanceCorrectionAdmin(admin.ModelAdmin):
    list_display = ('record', 'old_status', 'new_status', 'corrected_by', 'corrected_at')


@admin.register(models.PromotionRecord)
class PromotionRecordAdmin(admin.ModelAdmin):
    list_display = ('student', 'academic_year', 'from_class', 'to_class', 'decision')


@admin.register(models.AcademicCalendarEvent)
class AcademicCalendarEventAdmin(admin.ModelAdmin):
    list_display = ('title', 'school', 'event_type', 'start_date', 'end_date')
    list_filter = ('school', 'event_type')


@admin.register(models.Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('title', 'school', 'audience', 'is_published', 'published_at')
    list_filter = ('school', 'audience', 'is_published')


@admin.register(models.TerminalReportField)
class TerminalReportFieldAdmin(admin.ModelAdmin):
    list_display = ('school', 'key', 'label', 'display_order', 'is_visible')
    list_filter = ('school', 'is_visible')


@admin.register(models.UpdateRequest)
class UpdateRequestAdmin(admin.ModelAdmin):
    list_display = ('title', 'school', 'category', 'priority', 'status',
                    'assigned_to', 'due_date', 'created_at')
    list_filter = ('school', 'status', 'category', 'priority')
    search_fields = ('title', 'description', 'assigned_to__username')


@admin.register(models.UpdateRequestEvent)
class UpdateRequestEventAdmin(admin.ModelAdmin):
    list_display = ('request', 'user', 'action', 'created_at')
    list_filter = ('action',)
    raw_id_fields = ('request', 'user')


@admin.register(models.MarkCorrection)
class MarkCorrectionAdmin(admin.ModelAdmin):
    list_display = ('school_class', 'term', 'reason', 'status', 'initiated_by',
                    'initiated_at', 'completed_at')
    list_filter = ('school', 'status')
    search_fields = ('reason',)


@admin.register(models.AcademicYearOverride)
class AcademicYearOverrideAdmin(admin.ModelAdmin):
    list_display = ('school', 'academic_year', 'term', 'enabled_by',
                    'enabled_at', 'expires_at', 'applies_to_all', 'disabled_at')
    list_filter = ('school', 'applies_to_all', 'disabled_at')
    search_fields = ('reason', 'academic_year__name')
    filter_horizontal = ('teachers',)
