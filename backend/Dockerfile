FROM python:3.10-slim

WORKDIR /app

# Copy requirements from the backend directory (relative to project root)
COPY backend/requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend source code contents into /app
COPY backend/ .

# Expose port
EXPOSE 8000

# Command to run the application (server.py is now in /app)
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
