# backend/api/models.py

import uuid
from django.db import models


class AnalysisJob(models.Model):

    class Status(models.TextChoices):
        PENDING    = 'PENDING',    'Pending'
        PROCESSING = 'PROCESSING', 'Processing'
        COMPLETED  = 'COMPLETED',  'Completed'
        FAILED     = 'FAILED',     'Failed'

    # Public-facing identifier — never expose raw integer PKs in APIs.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    repo_url   = models.URLField(max_length=500)
    status     = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    # Populated by the Celery task on success.
    graph_data = models.JSONField(null=True, blank=True)
    summary    = models.TextField(null=True, blank=True)

    # Populated by the Celery task on failure.
    error_message = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.repo_url} [{self.status}]'