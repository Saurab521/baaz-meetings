FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt /app/
RUN apt-get update && apt-get install -y gcc curl && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get remove -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY . /app
CMD ["gunicorn", "-b", "0.0.0.0:8000", "app:app", "--workers=1", "--worker-class", "gthread"]
