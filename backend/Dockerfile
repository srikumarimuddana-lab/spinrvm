# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12.9-slim AS runtime

RUN groupadd --gid 1001 spinr \
    && useradd --uid 1001 --gid spinr --shell /bin/bash --create-home spinr

WORKDIR /app

# FIX: Copy from the specific site-packages folder in builder to the runtime's site-packages
COPY --from=builder /install/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
# Also copy the uvicorn binary if it was installed there
COPY --from=builder /install/bin /usr/local/bin

COPY --chown=spinr:spinr . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

USER spinr

EXPOSE 8000

# FIX: Simplified CMD (removed 'cd /app' since WORKDIR is /app)
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
