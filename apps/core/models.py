from django.db import models


class SequenceCounter(models.Model):
    """Auto-incrementing counter for generating unique business numbers."""
    prefix = models.CharField(max_length=20, unique=True)
    counter = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Sequence Counter'
        verbose_name_plural = 'Sequence Counters'

    def __str__(self):
        return f"{self.prefix}: {self.counter}"
