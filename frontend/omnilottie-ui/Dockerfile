# OmniLottie UI - Lightweight Frontend Container
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY static/ static/
COPY templates/ templates/

EXPOSE 7868

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7868"]
