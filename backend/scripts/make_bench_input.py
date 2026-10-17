"""Генератор большого JSON-файла с адресами для нагрузочных проверок API.

    python3 scripts/make_bench_input.py --count 5000

Файл совместим с `POST /api/v1/parse/batch` и с загрузкой во фронтенде:
массив `[{"id": "...", "raw_address": "..."}]`.

Данные детерминированы: одинаковый `--seed` даёт одинаковый файл.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import make_records  # noqa: E402

# Отдельные «злые» адреса: склейки, казахские буквы, дроби, микрорайоны,
# неоднозначные улицы без города и заведомо неизвестные названия.
EDGE_ADDRESSES = [
    "город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70",
    "Туран 55/6 магазин береке 4подъезд 9этаж Астана",
    "Астана, Тұран даңғылы 55/6",
    "Алматы, Абая 1/2",
    "Алматы, Абая 12",
    "Мкр Орбита-1, улица Абая 12",
    "Актобе/Ақтөбе қаласы, 12 микрорайон, дом 5, flat191",
    "Семей, Кабанбай батыра 10",
    "Шымкент, район Туран, проспект Тауке хана 1",
    "Атлантида, Неизвестная 5",
    "Неизвестная 5",
    "Алматы, улица Кабанбай батыра 999, подъезд 4, этаж 9",
]


def build(count: int, seed: int) -> list[dict[str, str]]:
    if count <= 0:
        raise ValueError("count должен быть больше нуля")

    edge = EDGE_ADDRESSES[:count]
    generated = make_records(count - len(edge), seed=seed)
    records = [
        {"id": f"edge_{index:03d}", "raw_address": address}
        for index, address in enumerate(edge, start=1)
    ]
    records.extend(
        {"id": record_id, "raw_address": address}
        for record_id, address in generated
    )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Сгенерировать JSON с большой пачкой адресов")
    parser.add_argument("--count", type=int, default=5000, help="количество записей")
    parser.add_argument("--seed", type=int, default=2026, help="seed генератора")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "benchmarks" / "input_5000.json",
        help="путь к JSON-файлу",
    )
    args = parser.parse_args()

    records = build(args.count, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    size_kb = args.out.stat().st_size / 1024
    print(f"Записано {len(records)} адресов: {args.out} ({size_kb:.1f} КиБ)")


if __name__ == "__main__":
    main()
