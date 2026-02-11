FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 7865

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7865"]
