# syntax=docker/dockerfile:1
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PORT=8081
WORKDIR /app
RUN addgroup --system indexer && adduser --system --ingroup indexer --home /app indexer
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY easynews_indexer ./easynews_indexer
COPY server.py easynews_client.py VERSION ./
USER indexer
EXPOSE 8081
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8081')+'/healthz', timeout=3).read()" || exit 1
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --worker-class gthread --workers ${GUNICORN_WORKERS:-2} --threads ${GUNICORN_THREADS:-8} --timeout ${GUNICORN_TIMEOUT:-45} --graceful-timeout 30 --keep-alive 5 --max-requests 5000 --max-requests-jitter 500 server:APP"]
