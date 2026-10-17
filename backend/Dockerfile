# --- сборка зависимостей (без интернета на этапе запуска) -------------------
FROM python:3.12-slim AS builder

WORKDIR /wheels
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# --- рантайм ---------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    ADDRESS_DB_PATH=/data/address_base.db \
    PARSER_WORKERS=4 \
    LOG_LEVEL=INFO

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY app ./app
COPY db ./db

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8000

# Проверка живости без внешних утилит (образ работает автономно).
# Контейнер здоров только если API отвечает и база адресов загружена.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import json,sys,urllib.request; \
p=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)); \
sys.exit(0 if p.get('status')=='ok' and p.get('db_loaded') is True else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
