FROM python:3.12-slim

RUN groupadd --gid 1001 spinr \
    && useradd --uid 1001 --gid spinr --shell /bin/bash --create-home spinr

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=spinr:spinr . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

USER spinr

EXPOSE 8000

# ── Healthcheck ────────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')" || exit 1

CMD ["sh", "-c", "cd /app && python -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
