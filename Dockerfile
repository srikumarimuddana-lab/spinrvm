# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12.9-slim AS runtime

# Security: run as non-root
RUN groupadd --gid 1001 spinr \
    && useradd --uid 1001 --gid spinr --shell /bin/bash --create-home spinr

WORKDIR /app

# Copy packages from builder directly into system site-packages
COPY --from=builder /install/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /install/bin /usr/local/bin

# Copy application source
COPY --chown=spinr:spinr . .

# Explicitly add /app to PYTHONPATH
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

USER spinr

EXPOSE 8000

# SIMPLIFIED CMD: Avoid "sh -c" and "cd" if possible, as it can obscure paths
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
