# backend/api/tasks.py

from celery import shared_task
from .models import AnalysisJob


@shared_task
def run_analysis(job_id: str) -> None:
    """
    Placeholder Celery task that transitions the job through its lifecycle.
    The actual clone → parse → LLM logic will be added in a later iteration.
    """
    try:
        job = AnalysisJob.objects.get(pk=job_id)
        job.status = AnalysisJob.Status.PROCESSING
        job.save(update_fields=['status', 'updated_at'])

        # ---------------------------------------------------------------
        # TODO: replace the block below with real implementation steps:
        #   1. Clone the repo to a temp directory
        #   2. Walk the source files and build the dependency graph via ast
        #   3. Call the LLM for an architectural summary
        #   4. Persist graph_data + summary, set status = COMPLETED
        # ---------------------------------------------------------------

        job.status = AnalysisJob.Status.COMPLETED
        job.graph_data = {}   # will be a real graph payload later
        job.summary = ''      # will be the LLM response later
        job.save(update_fields=['status', 'graph_data', 'summary', 'updated_at'])

    except AnalysisJob.DoesNotExist:
        # Nothing to update — log and exit cleanly.
        return

    except Exception as exc:
        job.status = AnalysisJob.Status.FAILED
        job.error_message = str(exc)
        job.save(update_fields=['status', 'error_message', 'updated_at'])
        # Re-raise so Celery marks the task as FAILURE in its own result backend.
        raise