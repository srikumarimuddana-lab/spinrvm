FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY api.py controller.py policy.py runtime.py telemetry.py ./
USER 65532:65532
CMD ["python", "runtime.py"]
