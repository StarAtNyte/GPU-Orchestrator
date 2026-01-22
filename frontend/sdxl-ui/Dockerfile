# SDXL Frontend - Lightweight UI Container
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY static/ static/
COPY templates/ templates/

# Expose port
EXPOSE 7861

# Run the application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7861"]
