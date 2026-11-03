FROM python:3.12.7-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py validation.py forwarder.py processor.py ./

RUN useradd --system --uid 10001 bridge && mkdir -p /app/data && chown bridge /app/data
USER bridge

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "main.py", "--healthcheck"]

CMD ["python", "main.py"]
