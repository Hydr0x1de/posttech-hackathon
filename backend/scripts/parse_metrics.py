"""Метрики производительности разбора адресов по этапам конвейера.

Скрипт собирает:
1. разбивку времени одной записи по этапам (preprocess -> tail -> classify ->
   SQLite FTS5 -> matching -> enrichment -> confidence -> прочее);
2. масштабирование по потокам (1 vs 4, как в README);
3. скорость геокодинга уже разобранных записей.

Запуск (нужна production-БД или демо-БД):

    python3 scripts/parse_metrics.py \
      --db data/address_base.db \
      --input benchmarks/input_5000.json

Результат:
    benchmarks/parse_metrics.json
    benchmarks/parse_metrics.svg
    benchmarks/parse_metrics.png   (если установлен rsvg-convert)
    benchmarks/parse_metrics.md

Инструментирование сделано только в этом скрипте: production-код парсера
не меняется. Для замеров этапов скрипт временно оборачивает внутренние
функции парсера, а многопоточность и геокодинг измеряет до этого.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.services.parser as parser_mod  # noqa: E402
from app.repositories.repository import (  # noqa: E402
    NullAddressRepository,
    RepositoryManager,
)
from app.services.geocode import GeocoderService  # noqa: E402
from app.services.parser import AddressParserService  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# (ключ, подпись на графике, цвет)
STAGES: tuple[tuple[str, str, str], ...] = (
    ("preprocess", "1. Preprocess (WorkText, regex)", "#7d8590"),
    ("tail", "2. Tail parser (дом/кв/подъезд/этаж)", "#5ee27a"),
    ("classify", "3. Классификация (город/район/обл.)", "#5ee27a"),
    ("lookup", "4. SQLite FTS5 lookup ← C, вне GIL", "#39b9e8"),
    ("match", "5. Матчинг сущности + difflib", "#f4a33b"),
    ("enrich", "6. Обогащение (родители, тип)", "#9b8cff"),
    ("confidence", "7. Confidence", "#57d7c7"),
    ("other", "прочее", "#626b78"),
)


@dataclass(slots=True)
class Recorder:
    """Накопитель времён этапов. Используется только в однопоточном замере."""

    times: dict[str, float]

    def __init__(self) -> None:
        self.times = {key: 0.0 for key, _, _ in STAGES}

    def reset(self) -> None:
        for key in self.times:
            self.times[key] = 0.0

    def add(self, key: str, seconds: float) -> None:
        self.times[key] += seconds

    def wrap(self, key: str, function):
        def wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                self.add(key, time.perf_counter() - started)

        wrapped.__name__ = getattr(function, "__name__", key)
        return wrapped


def load_records(path: Path, limit: int | None) -> list[tuple[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: ожидается JSON-массив")
    records: list[tuple[str, str]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or "raw_address" not in item:
            raise ValueError(f"{path}: запись №{index + 1} без raw_address")
        records.append((str(item.get("id", f"rec_{index + 1}")), str(item["raw_address"])))
        if limit is not None and len(records) >= limit:
            break
    if not records:
        raise ValueError(f"{path}: пустой массив")
    return records


def make_provider(db: Path | None):
    if db is None:
        repository = NullAddressRepository()
        return lambda: repository, repository, "fallback (без БД)"
    manager = RepositoryManager(str(db), reload_interval=0)
    stats = manager.health()
    if not stats.get("db_loaded"):
        raise SystemExit(f"БД не загрузилась: {db}")
    return manager.get, manager.get(), f"{db} (fts={stats.get('fts')})"


def benchmark_threads(
    provider,
    records: list[tuple[str, str]],
    *,
    workers: int,
    repeat: int,
    warmup: int,
) -> dict[str, object]:
    service = AddressParserService(provider, workers=workers)
    if warmup > 0:
        service.parse_batch(records[:warmup])
    timings: list[float] = []
    for _ in range(repeat):
        started = time.perf_counter()
        service.parse_batch(records)
        timings.append(time.perf_counter() - started)
    best = min(timings)
    return {
        "workers": workers,
        "count": len(records),
        "best_seconds": best,
        "median_seconds": statistics.median(timings),
        "ms_per_record": best / len(records) * 1000,
        "runs_seconds": timings,
    }


def benchmark_geocode(
    provider,
    parser: AddressParserService,
    records: list[tuple[str, str]],
    *,
    count: int,
    warmup: int,
) -> dict[str, object]:
    parsed = parser.parse_batch(records[:count])
    geocoder = GeocoderService(provider, parser, workers=1)
    for item in parsed[:warmup]:
        geocoder.geocode(
            item.id,
            city=item.city,
            street_name=item.street_name,
            house=item.house,
        )
    started = time.perf_counter()
    for item in parsed:
        geocoder.geocode(
            item.id,
            city=item.city,
            street_name=item.street_name,
            house=item.house,
        )
    elapsed = time.perf_counter() - started
    return {
        "count": len(parsed),
        "seconds": elapsed,
        "ms_per_record": elapsed / len(parsed) * 1000,
    }


def profile_stages(
    provider,
    records: list[tuple[str, str]],
    *,
    count: int,
    warmup: int,
) -> tuple[list[dict[str, object]], float]:
    """Временно инструментирует парсер и считает среднее время этапов."""
    recorder = Recorder()
    originals: list[tuple[object, str, object]] = []

    # Статические методы класса.
    static_attrs = (
        ("_preprocess", "preprocess"),
        ("_apply_entity", "enrich"),
        ("_fill_province_from_city", "enrich"),
        ("_confidence", "confidence"),
    )
    for attr, key in static_attrs:
        original = getattr(AddressParserService, attr)
        originals.append((AddressParserService, attr, original))
        setattr(
            AddressParserService,
            attr,
            staticmethod(recorder.wrap(key, original)),
        )

    # Модульные функции.
    module_attrs = (
        ("parse_tail", "tail"),
        ("_match_classifiers", "classify"),
        ("_match_entity", "match"),
        ("_is_ambiguous_match", "match"),
        ("_has_unlinked_leading_candidate", "match"),
    )
    for attr, key in module_attrs:
        original = getattr(parser_mod, attr)
        originals.append((parser_mod, attr, original))
        setattr(parser_mod, attr, recorder.wrap(key, original))

    # Конструктор WorkText входит в preprocess.
    original_work_text = parser_mod.WorkText

    class TimedWorkText(original_work_text):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs) -> None:
            started = time.perf_counter()
            super().__init__(*args, **kwargs)
            recorder.add("preprocess", time.perf_counter() - started)

    parser_mod.WorkText = TimedWorkText

    # SQLite lookup на конкретном объекте репозитория.
    repository = provider()
    original_search = repository.search_streets
    repository.search_streets = recorder.wrap("lookup", original_search)  # type: ignore[method-assign]

    service = AddressParserService(provider, workers=1)
    if warmup > 0:
        service.parse_batch(records[:warmup])

    per_record: list[dict[str, float]] = []
    for record_id, raw in records[:count]:
        recorder.reset()
        started = time.perf_counter()
        service.parse(record_id, raw)
        total = time.perf_counter() - started
        measured = dict(recorder.times)
        measured["other"] = max(0.0, total - sum(measured.values()))
        measured["_total"] = total
        per_record.append(measured)

    # Восстанавливаем monkeypatch, чтобы скрипт можно было импортировать.
    parser_mod.WorkText = original_work_text
    repository.search_streets = original_search  # type: ignore[method-assign]
    for target, attr, original in reversed(originals):
        if target is AddressParserService:
            setattr(target, attr, staticmethod(original))
        else:
            setattr(target, attr, original)

    mean_total = statistics.mean(item["_total"] for item in per_record)
    stages: list[dict[str, object]] = []
    for key, label, color in STAGES:
        mean_seconds = statistics.mean(item[key] for item in per_record)
        stages.append(
            {
                "key": key,
                "label": label,
                "color": color,
                "ms_per_record": mean_seconds * 1000,
                "percent": mean_seconds / mean_total * 100 if mean_total else 0.0,
            }
        )
    return stages, mean_total * 1000


def nice_ceiling(value: float) -> float:
    if value <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(value))
    for factor in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        candidate = factor * magnitude
        if candidate >= value:
            return candidate
    return 10 * magnitude


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def render_svg(metrics: dict[str, object], path: Path) -> None:
    stages = metrics["stages"]
    total_ms = float(metrics["total_ms_per_record"])
    count = int(metrics["count"])
    threads = metrics["threads"]
    one = threads["1"]
    four = threads["4"]
    speedup = (
        (float(one["best_seconds"]) / float(four["best_seconds"]) - 1) * 100
        if float(four["best_seconds"])
        else 0.0
    )
    stage_by_key = {item["key"]: item for item in stages}
    lookup = stage_by_key["lookup"]
    matching = stage_by_key["match"]
    regex = float(stage_by_key["preprocess"]["percent"]) + float(stage_by_key["tail"]["percent"])

    width, height = 2000, 1050
    plot_left, plot_right = 610, 1460
    top, row_height = 235, 88
    base_y = top + row_height * len(stages)
    scale_max = nice_ceiling(total_ms * 1.12)
    scale = (plot_right - plot_left) / scale_max
    tick_step = 0.5 if scale_max <= 5 else 1.0
    ticks = []
    tick = 0.0
    while tick <= scale_max + 1e-9:
        ticks.append(tick)
        tick += tick_step

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#0b0d10"/>',
        '<style>'
        'text{font-family:"DejaVu Sans","Arial",sans-serif;fill:#e8eaed}'
        '.muted{fill:#8f97a3}.label{font-size:23px}.bar-label{font-size:22px;font-weight:700}'
        '.tick{font-size:19px}.title{font-size:46px;font-weight:700}'
        '.subtitle{font-size:23px;fill:#8f97a3}.panel-title{font-size:28px;font-weight:600}'
        '</style>',
        '<text class="title" x="60" y="72">'
        'Jana Address — где уходит время на разбор одного адреса</text>',
        f'<text class="subtitle" x="60" y="122">'
        f'1 поток · прогретая БД · батч {count} · всего {total_ms:.2f} мс/запись | '
        f'{float(lookup["percent"]):.0f}% — SQLite FTS5 (C) · '
        f'{float(matching["percent"]):.0f}% — Python-матчинг · '
        f'{regex:.0f}% — regex</text>',
    ]

    # Сетка и подписи оси X.
    for tick_value in ticks:
        x = plot_left + tick_value * scale
        parts.append(
            f'<line x1="{x:.1f}" y1="{top - 30}" x2="{x:.1f}" y2="{base_y + 8}" '
            'stroke="#252b33" stroke-width="1"/>'
        )
        parts.append(
            f'<text class="tick muted" x="{x:.1f}" y="{base_y + 42}" text-anchor="middle">'
            f'{tick_value:g}</text>'
        )
    parts.append(
        f'<text class="tick muted" x="{(plot_left + plot_right) / 2:.1f}" y="{base_y + 82}" '
        'text-anchor="middle">накопленное время на одну запись, мс</text>'
    )

    # Горизонтальные бары.
    for index, item in enumerate(stages):
        y = top + index * row_height
        label_x = 60
        bar_y = y - 21
        value = float(item["ms_per_record"])
        bar_width = max(2.0, value * scale)
        color = str(item["color"])
        parts.append(
            f'<text class="label" x="{label_x}" y="{y + 8}">{esc(item["label"])}</text>'
        )
        parts.append(
            f'<rect x="{plot_left}" y="{bar_y}" width="{bar_width:.1f}" height="42" '
            f'fill="{color}" rx="1"/>'
        )
        text_x = plot_left + bar_width + 18
        parts.append(
            f'<text class="bar-label" x="{text_x:.1f}" y="{y + 8}">'
            f'{value:.2f} мс · {float(item["percent"]):.0f}%</text>'
        )

    # Итоговая линия.
    total_x = plot_left + total_ms * scale
    parts.append(
        f'<line x1="{total_x:.1f}" y1="{top - 42}" x2="{total_x:.1f}" y2="{base_y + 8}" '
        'stroke="#f2b84b" stroke-width="2" stroke-dasharray="7 6"/>'
    )
    parts.append(
        f'<text class="bar-label" x="{total_x - 8:.1f}" y="{top - 55}" text-anchor="end" '
        f'fill="#f2b84b">итого {total_ms:.2f} мс</text>'
    )

    # Панель многопоточности.
    panel_left, panel_right = 1600, 1950
    panel_top, panel_bottom = 250, 875
    parts.append(
        f'<text class="panel-title" x="{panel_left}" y="185">Многопоточность (GIL)</text>'
    )
    parts.append(
        f'<text class="bar-label" x="{panel_left}" y="240" fill="#f2b84b">'
        f'+{speedup:.0f}% от 4 потоков</text>'
    )
    max_seconds = max(float(one["best_seconds"]), float(four["best_seconds"]), 1e-9) * 1.15
    for idx, (label, item, color) in enumerate(
        (
            ("1 поток", one, "#aeb6c2"),
            ("4 потока", four, "#39b9e8"),
        )
    ):
        bar_height = float(item["best_seconds"]) / max_seconds * (panel_bottom - panel_top)
        x = panel_left + 45 + idx * 150
        y = panel_bottom - bar_height
        parts.append(
            f'<rect x="{x}" y="{y:.1f}" width="95" height="{bar_height:.1f}" '
            f'fill="{color}" rx="3"/>'
        )
        parts.append(
            f'<text class="bar-label" x="{x + 47:.1f}" y="{y - 18:.1f}" '
            f'text-anchor="middle">{float(item["best_seconds"]):.2f} c</text>'
        )
        parts.append(
            f'<text class="label muted" x="{x + 47:.1f}" y="{panel_bottom + 38}" '
            f'text-anchor="middle">{label}</text>'
        )
    parts.append(
        f'<line x1="{panel_left}" y1="{panel_bottom}" x2="{panel_right}" y2="{panel_bottom}" '
        'stroke="#353b45" stroke-width="1"/>'
    )

    geocode = metrics["geocode"]
    parts.append(
        f'<text class="tick muted" x="{panel_right}" y="{height - 34}" text-anchor="end">'
        f'геокодинг: {float(geocode["seconds"]):.1f} c / {int(geocode["count"])} · '
        f'{float(geocode["ms_per_record"]):.1f} мс/запись</text>'
    )
    parts.append(
        f'<text class="tick muted" x="60" y="{height - 34}">'
        f'backend/scripts/parse_metrics.py · {esc(metrics["generated_at"])}</text>'
    )
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def render_markdown(metrics: dict[str, object], path: Path) -> None:
    lines = [
        "# Метрики разбора адресов",
        "",
        f"Сгенерировано: `{metrics['generated_at']}`",
        "",
        f"- БД: `{metrics['db']}`",
        f"- Вход: `{metrics['input']}`",
        f"- Записей в замере этапов: **{metrics['count']}**",
        f"- Итого: **{float(metrics['total_ms_per_record']):.2f} мс/запись**",
        "",
        "| Этап | мс/запись | Доля |",
        "|---|---:|---:|",
    ]
    for item in metrics["stages"]:
        lines.append(
            f"| {item['label']} | {float(item['ms_per_record']):.2f} | "
            f"{float(item['percent']):.0f}% |"
        )
    threads = metrics["threads"]
    one, four = threads["1"], threads["4"]
    speedup = (float(one["best_seconds"]) / float(four["best_seconds"]) - 1) * 100
    lines.extend(
        [
            "",
            "## Потоки",
            "",
            "| Потоков | Лучшее время | мс/запись |",
            "|---:|---:|---:|",
            f"| 1 | {float(one['best_seconds']):.3f} c | {float(one['ms_per_record']):.2f} |",
            f"| 4 | {float(four['best_seconds']):.3f} c | {float(four['ms_per_record']):.2f} |",
            "",
            f"Ускорение на 4 потоках: **+{speedup:.0f}%**.",
            "",
            "## Геокодинг",
            "",
            f"{float(metrics['geocode']['seconds']):.1f} c на "
            f"{int(metrics['geocode']['count'])} записей "
            f"({float(metrics['geocode']['ms_per_record']):.1f} мс/запись).",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Метрики производительности разбора")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="путь к address_base.db (по умолчанию fallback без БД)",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "benchmarks" / "input_5000.json",
        help="JSON-массив адресов",
    )
    parser.add_argument("--count", type=int, default=2000, help="записей для этапов/потоков")
    parser.add_argument("--repeat", type=int, default=3, help="повторов для потоков")
    parser.add_argument("--warmup", type=int, default=200, help="прогрев")
    parser.add_argument("--geocode-count", type=int, default=1000, help="записей для геокодинга")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "benchmarks",
        help="куда сохранить json/svg/png/md",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Нет входного файла: {args.input}")
    records = load_records(args.input, None)
    if len(records) < args.count:
        raise SystemExit(f"В {args.input} только {len(records)} записей, нужно {args.count}")

    provider, _repository, db_label = make_provider(args.db)

    # 1. Многопоточность — до инструментирования, чтобы не мешать замерам.
    thread_records = records[: args.count]
    thread_one = benchmark_threads(
        provider, thread_records, workers=1, repeat=args.repeat, warmup=args.warmup
    )
    thread_four = benchmark_threads(
        provider, thread_records, workers=4, repeat=args.repeat, warmup=args.warmup
    )

    # 2. Геокодинг — тоже до инструментирования.
    geocode_parser = AddressParserService(provider, workers=4)
    geocode = benchmark_geocode(
        provider,
        geocode_parser,
        records,
        count=min(args.geocode_count, len(records)),
        warmup=min(50, args.geocode_count),
    )

    # 3. Этапы — однопоточно, с временными обёртками.
    stages, total_ms = profile_stages(
        provider,
        records,
        count=args.count,
        warmup=args.warmup,
    )

    metrics = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "db": db_label,
        "input": str(args.input),
        "input_records": len(records),
        "count": args.count,
        "thread_count": len(thread_records),
        "warmup": args.warmup,
        "total_ms_per_record": total_ms,
        "stages": stages,
        "threads": {
            "1": {k: v for k, v in thread_one.items() if k != "workers"},
            "4": {k: v for k, v in thread_four.items() if k != "workers"},
        },
        "geocode": geocode,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "parse_metrics.json"
    svg_path = args.out_dir / "parse_metrics.svg"
    png_path = args.out_dir / "parse_metrics.png"
    md_path = args.out_dir / "parse_metrics.md"

    json_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    render_svg(metrics, svg_path)
    render_markdown(metrics, md_path)

    rsvg = shutil.which("rsvg-convert")
    if rsvg:
        subprocess.run(
            [rsvg, "-w", "2000", "-o", str(png_path), str(svg_path)],
            check=False,
        )

    print(f"Всего: {total_ms:.2f} мс/запись на {args.count} записей")
    for item in stages:
        print(
            f"  {item['label']}: {float(item['ms_per_record']):.2f} мс "
            f"({float(item['percent']):.0f}%)"
        )
    print(
        f"Потоки: 1 → {float(thread_one['best_seconds']):.3f} c, "
        f"4 → {float(thread_four['best_seconds']):.3f} c"
    )
    print(f"Геокодинг: {float(geocode['seconds']):.1f} c / {geocode['count']}")
    print(f"JSON: {json_path}")
    print(f"SVG:  {svg_path}")
    if png_path.exists():
        print(f"PNG:  {png_path}")


if __name__ == "__main__":
    main()
