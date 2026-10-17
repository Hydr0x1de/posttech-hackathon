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

# curl is required by the entrypoint to fetch the DB on a clean clone.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY app ./app
COPY db ./db
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod 0755 /usr/local/bin/entrypoint.sh \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8000

# Проверка живости без внешних утилит (образ работает автономно).
# По умолчанию контейнер здоров и в fallback-режиме; /health показывает
# db_loaded. Для строгого режима задайте ADDRESS_DB_REQUIRED=true.
# start-period увеличен: на чистом клоне БД может скачиваться с GitHub.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=5 \
    CMD python -c "import json,os,sys,urllib.request; \
p=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)); \
required=os.getenv('ADDRESS_DB_REQUIRED','false').lower()=='true'; \
print('db_loaded=%s required=%s' % (p.get('db_loaded'), required)); \
sys.exit(0 if p.get('status')=='ok' and (p.get('db_loaded') is True or not required) else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
