FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN useradd -m app && mkdir -p /tmp/pdfstudio_jobs && chown -R app /app /tmp/pdfstudio_jobs
USER app
EXPOSE 8000
# 1 worker + threads suits small servers (512 MB). Raise WEB_CONCURRENCY on bigger ones.
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --threads 4 --timeout 300 --access-logfile -
