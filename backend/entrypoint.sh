#!/bin/sh
# Prepare the address base before FastAPI starts.
#
# Priority:
#   1. /data/address_base.db already exists (named volume, previous run);
#   2. local seed ./backend/data/address_base.db mounted read-only at /seed;
#   3. download the release asset from GitHub.
set -eu

DB_PATH="${ADDRESS_DB_PATH:-/data/address_base.db}"
DB_URL="${ADDRESS_DB_URL:-https://github.com/Hydr0x1de/posttech-hackathon/releases/download/v1.0/address_base.db}"
SEED_PATH="${ADDRESS_DB_SEED:-/seed/address_base.db}"
PART_PATH="${DB_PATH}.part"

if [ ! -s "$DB_PATH" ]; then
    mkdir -p "$(dirname "$DB_PATH")"
    rm -f "$PART_PATH"

    if [ -s "$SEED_PATH" ]; then
        echo "База данных не найдена. Копируем локальную БД из $SEED_PATH..."
        cp "$SEED_PATH" "$PART_PATH"
    else
        echo "База данных не найдена. Скачиваем актуальную БД..."
        echo "URL: $DB_URL"
        if ! curl -fL --retry 5 --retry-delay 5 --connect-timeout 20 \
            --progress-bar -o "$PART_PATH" "$DB_URL"; then
            echo "Не удалось скачать БД. Проверьте ADDRESS_DB_URL и сеть." >&2
            rm -f "$PART_PATH"
            exit 1
        fi
    fi

    # A GitHub error page or truncated download must not be treated as SQLite.
    if [ "$(head -c 15 "$PART_PATH")" != "SQLite format 3" ]; then
        echo "Скачанный файл не является SQLite-базой. Удаляю $PART_PATH." >&2
        rm -f "$PART_PATH"
        exit 1
    fi

    mv "$PART_PATH" "$DB_PATH"
    echo "База данных готова: $DB_PATH ($(du -h "$DB_PATH" | cut -f1))"
fi

exec "$@"
