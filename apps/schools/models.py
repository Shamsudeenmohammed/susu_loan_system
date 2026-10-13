from django.db import models


class School(models.Model):
    """A branch/school operated under the same organisation.

    Every school-owned record (students, classes, academic years, students,
    fee structures, fee accounts, payments, receipts, reminders, ...) points at
    one of these rows so that data can be isolated per school and reported on
    either individually or in combination by the proprietor.
    """

    name = models.CharField(max_length=200, unique=True)
    short_name = models.CharField(max_length=100, blank=True)
    code = models.CharField(
        max_length=30,
        unique=True,
        help_text='Short unique code used in references (e.g. MANKARIGU).',
    )
    location = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'School'
        verbose_name_plural = 'Schools'

    def __str__(self):
        return self.name

    @property
    def display_name(self):
        return self.short_name or self.name
