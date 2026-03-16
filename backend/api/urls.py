from django.urls import path
from . import views

urlpatterns = [
    path('jobs/',                          views.submit_job,     name='submit_job'),
    path('jobs/<uuid:job_id>/',            views.poll_job,       name='poll_job'),
    path('jobs/<uuid:job_id>/summary-stream/', views.stream_summary, name='stream_summary'),
]