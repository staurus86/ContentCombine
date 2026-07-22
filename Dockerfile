FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --target=/deps -r requirements.txt

FROM python:3.11-slim

ENV PYTHONPATH=/deps
# glibc malloc: fewer arenas + aggressive trim → меньше RSS-фрагментации от короткоживущих потоков
# (HTTP-сервер + парсинг). gc.collect() чистит Python-объекты, но арены ОС не отдаёт — это делает trim.
ENV MALLOC_ARENA_MAX=2
ENV MALLOC_TRIM_THRESHOLD_=100000
COPY --from=builder /deps /deps

WORKDIR /app
COPY . .

CMD ["python", "main.py"]
