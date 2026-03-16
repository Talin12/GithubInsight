# backend/api/views.py

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import AnalysisJob
from .tasks import run_analysis


@api_view(['POST'])
def submit_job(request):
    """
    POST /api/jobs/
    Body: { "repo_url": "https://github.com/owner/repo" }

    Creates an AnalysisJob, enqueues the Celery task, and returns the job ID.
    """
    repo_url = request.data.get('repo_url', '').strip()

    if not repo_url:
        return Response(
            {'error': 'repo_url is required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    job = AnalysisJob.objects.create(repo_url=repo_url)
    run_analysis.delay(str(job.id))

    return Response(
        {'job_id': str(job.id)},
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(['GET'])
def poll_job(request, job_id):
    """
    GET /api/jobs/<job_id>/

    Returns the current status (and results, if ready) for a given job ID.
    """
    try:
        job = AnalysisJob.objects.get(pk=job_id)
    except AnalysisJob.DoesNotExist:
        return Response(
            {'error': 'Job not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    payload = {
        'job_id':     str(job.id),
        'status':     job.status,
        'repo_url':   job.repo_url,
        'created_at': job.created_at,
        'updated_at': job.updated_at,
    }

    if job.status == AnalysisJob.Status.COMPLETED:
        payload['graph_data'] = job.graph_data
        payload['summary']    = job.summary

    if job.status == AnalysisJob.Status.FAILED:
        payload['error_message'] = job.error_message

    return Response(payload, status=status.HTTP_200_OK)