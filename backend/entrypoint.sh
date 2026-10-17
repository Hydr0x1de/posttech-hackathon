#!/bin/sh
# Prepare the address base before FastAPI starts.
#
# Priority:
#   1. /data/address_base.db already exists (named volume, previous run);
#   2. local seed ./backend/data/address_base.db mounted read-only at /seed;
#   3. GitHub Release asset;
#   4. fallback CDN (domain mirror) if GitHub is unavailable.
set -eu

DB_PATH="${ADDRESS_DB_PATH:-/data/address_base.db}"
DB_URL="${ADDRESS_DB_URL:-https://github.com/Hydr0x1de/posttech-hackathon/releases/download/v1.0/address_base.db}"
DB_FALLBACK_URL="${ADDRESS_DB_FALLBACK_URL:-https://posttech_db.hydr0x1de.xyz/address_base.db}"
SEED_PATH="${ADDRESS_DB_SEED:-/seed/address_base.db}"
PART_PATH="${DB_PATH}.part"

is_sqlite() {
    [ -s "$1" ] && [ "$(head -c 15 "$1")" = "SQLite format 3" ]
}

fetch_db() {
    url="$1"
    label="$2"
    rm -f "$PART_PATH"
    echo "Скачиваем БД ($label): $url"
    echo "Скачивание 247 MB может занять 1–2 минуты..."
    # -L follows redirects (including http -> https for mirror URLs).
    if ! curl -fsSL --retry 3 --retry-delay 3 --connect-timeout 15 \
        -o "$PART_PATH" "$url"; then
        echo "Источник недоступен: $url" >&2
        rm -f "$PART_PATH"
        return 1
    fi
    if ! is_sqlite "$PART_PATH"; then
        echo "Источник вернул не SQLite-базу: $url" >&2
        rm -f "$PART_PATH"
        return 1
    fi
    return 0
}

# A partially written file from an interrupted run must not reach SQLite.
if [ -s "$DB_PATH" ] && ! is_sqlite "$DB_PATH"; then
    echo "Найдена повреждённая БД $DB_PATH — пересоздаём." >&2
    rm -f "$DB_PATH"
fi

if [ ! -s "$DB_PATH" ]; then
    mkdir -p "$(dirname "$DB_PATH")"

    if [ -s "$SEED_PATH" ] && is_sqlite "$SEED_PATH"; then
        echo "База данных не найдена. Копируем локальную БД из $SEED_PATH..."
        cp "$SEED_PATH" "$PART_PATH"
    else
        echo "База данных не найдена."
        if ! fetch_db "$DB_URL" "GitHub Release"; then
            echo "GitHub недоступен, пробуем резервный источник..." >&2
            if ! fetch_db "$DB_FALLBACK_URL" "fallback CDN"; then
                if [ "${ADDRESS_DB_REQUIRED:-false}" = "true" ]; then
                    echo "Не удалось скачать БД ни с GitHub, ни с резервного источника." >&2
                    echo "ADDRESS_DB_REQUIRED=true — завершаю запуск." >&2
                    exit 1
                fi
                echo "ВНИМАНИЕ: БД не загружена. API запустится в fallback-режиме" >&2
                echo "(db_loaded=false, regex-разбор с confidence=0.5)." >&2
            fi
        fi
    fi

    if [ -s "$PART_PATH" ]; then
        if ! is_sqlite "$PART_PATH"; then
            echo "Файл не является SQLite-базой. Удаляю $PART_PATH." >&2
            rm -f "$PART_PATH"
            exit 1
        fi
        mv "$PART_PATH" "$DB_PATH"
        echo "База данных готова: $DB_PATH ($(du -h "$DB_PATH" | cut -f1))"
    fi
fi

exec "$@"
