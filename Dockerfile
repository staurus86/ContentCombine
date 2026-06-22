FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --target=/deps -r requirements.txt

FROM python:3.11-slim

ENV PYTHONPATH=/deps
COPY --from=builder /deps /deps

WORKDIR /app
COPY . .

CMD ["python", "main.py"]
