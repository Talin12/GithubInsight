# backend/api/views.py

import os
import time

import redis
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import AnalysisJob
from .tasks import run_analysis


def _get_redis():
    return redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))


@api_view(['POST'])
def submit_job(request):
    """POST /api/jobs/"""
    repo_url = request.data.get('repo_url', '').strip()
    if not repo_url:
        return Response({'error': 'repo_url is required.'}, status=status.HTTP_400_BAD_REQUEST)

    # Cache check — return existing completed job if available.
    existing = AnalysisJob.objects.filter(
        repo_url=repo_url,
        status=AnalysisJob.Status.COMPLETED,
    ).order_by('-created_at').first()

    if existing:
        return Response(
            {'job_id': str(existing.id), 'cached': True},
            status=status.HTTP_200_OK,
        )

    job = AnalysisJob.objects.create(repo_url=repo_url)
    run_analysis.delay(str(job.id))
    return Response({'job_id': str(job.id), 'cached': False}, status=status.HTTP_202_ACCEPTED)


@api_view(['GET'])
def poll_job(request, job_id):
    """GET /api/jobs/<job_id>/"""
    try:
        job = AnalysisJob.objects.get(pk=job_id)
    except AnalysisJob.DoesNotExist:
        return Response({'error': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)

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


def stream_summary(request, job_id):
    """GET /api/jobs/<job_id>/summary-stream/"""
    r = _get_redis()
    stream_key = f'summary_stream:{job_id}'

    def event_stream():
        timeout_at = time.time() + 300

        while time.time() < timeout_at:
            result = r.blpop(stream_key, timeout=5)
            if result is None:
                yield 'event: heartbeat\ndata: {}\n\n'
                continue

            _, raw = result
            chunk = raw.decode('utf-8') if isinstance(raw, bytes) else raw

            if chunk == '__DONE__':
                yield 'event: done\ndata: {}\n\n'
                return
            if chunk.startswith('__ERROR__:'):
                yield f'event: error\ndata: {chunk[len("__ERROR__:"):]}\n\n'
                return

            yield f'data: {chunk.replace(chr(10), "\\n")}\n\n'

        yield 'event: error\ndata: Stream timed out.\n\n'

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control']               = 'no-cache'
    response['X-Accel-Buffering']           = 'no'
    response['Access-Control-Allow-Origin'] = 'http://localhost:5173'
    return response