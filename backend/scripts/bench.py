"""Замер производительности: 1000 записей через пакетный разбор.

    python scripts/bench.py --count 1000 --db data/address_base.db

Без аргумента `--db` проверяется fallback-режим (без базы).
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.repositories.repository import NullAddressRepository, RepositoryManager  # noqa: E402
from app.services.parser import AddressParserService  # noqa: E402

PROVINCES = ["", "Актюбинская область, ", "Алматинская область, ", "Карагандинская область, "]
CITIES = ["Актобе", "Алматы", "Астана", "Караганда", "Шымкент"]
DISTRICTS = ["", "Ауэзовский район, ", "Илийский район, "]
STREETS = [
    "ул. Абая", "пр. Абилкайыр хана", "ул. Казыбек би", "мкр 12", "квартал 66",
    "ул. Фурманова", "пр. Мангилик Ел", "ул. Сарыарка", "ул. Толе би", "8 марта",
]
TAILS = [
    "д. {h}", "дом {h}", "{h}", "{h}/6", "{h}а", "{h}, кв {a}", "{h}, подъезд {e}, этаж {f}",
    "{h} кв {a} под {e} эт {f}", "{h}дом {e}подьезд {a}кв", "{h} {e} {f}",
    "{h}, магазин Береке, кв {a}", "{h}, Qazpost, эт {f}",
]


def make_records(count: int, seed: int = 42) -> list[tuple[str, str]]:
    rnd = random.Random(seed)
    records: list[tuple[str, str]] = []
    for index in range(count):
        parts = [
            rnd.choice(PROVINCES),
            rnd.choice(CITIES),
            ", ",
            rnd.choice(DISTRICTS),
            rnd.choice(STREETS),
            " ",
            rnd.choice(TAILS).format(
                h=rnd.randint(1, 120), a=rnd.randint(1, 300),
                e=rnd.randint(1, 8), f=rnd.randint(1, 25),
            ),
        ]
        records.append((f"rec_{index:05d}", "".join(parts)))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Бенчмарк пакетного разбора адресов")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--db", default=None, help="путь к address_base.db (по умолчанию fallback)")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    if args.db:
        manager = RepositoryManager(args.db, reload_interval=0)
        provider = manager.get
        stats = manager.health()
    else:
        provider = NullAddressRepository
        stats = NullAddressRepository().stats()

    service = AddressParserService(provider, workers=args.workers)

    print(f"Режим: {'БД ' + args.db if args.db else 'fallback (без БД)'} | записей: {args.count}")
    print(f"Состояние БД: {stats}")

    timings: list[float] = []
    for run in range(args.repeat):
        # Свой seed на прогон: кэш разобранных адресов не «съедает» замер.
        records = make_records(args.count, seed=42 + run * 7)
        started = time.perf_counter()
        results = service.parse_batch(records)
        elapsed = time.perf_counter() - started
        timings.append(elapsed)
        print(f"  прогон {run + 1}: {elapsed:.3f} c ({elapsed / args.count * 1000:.2f} мс/запись)")

    best = min(timings)
    print(f"Итого: min={best:.3f} c, среднее={statistics.mean(timings):.3f} c, "
          f"медиана={statistics.median(timings):.3f} c")
    print(f"Лимит ТЗ (5 мин = 300 c): {'OK' if best < 300 else 'ПРЕВЫШЕН'}")
    filled = sum(
        1 for item in results
        if any([item.city, item.street_name, item.living_area, item.house])
    )
    print(f"Заполнено хотя бы одно поле: {filled}/{len(results)}")


if __name__ == "__main__":
    main()
