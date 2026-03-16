# Dockerfile.backend

FROM python:3.12-slim

# git is required for cloning repos at runtime
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Collect static files for the admin, then expose port.
RUN python manage.py collectstatic --noinput

EXPOSE 8000