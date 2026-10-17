#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_osm_db.py — автономный ETL: OSM PBF (Казахстан) + КАТО -> address_base.db

Пайплайн (потоковый, RAM < 2 GB, pyosmium + shapely + sqlite3):

  pass 1  FileProcessor.with_areas()        границы: KZ (admin_level=2),
                                            provinces (4), districts (6),
                                            level8-акиматы, place-ареалы
  pass 2  FileProcessor(entities=NODE)      place=city|town (и др.) — точки
  -- сборка иерархии + запись provinces/cities/districts в SQLite --
  pass 3  FileProcessor.with_locations()    именованные дороги, микрорайоны,
                                            дома (addr:housenumber)

Запуск:
  python3 build_osm_db.py --pbf kazakhstan-latest.osm.pbf \
                          --kato kato.json \
                          --db address_base.db

ВНИМАНИЕ (грабли pyosmium): `o.type_str` — это МЕТОД, а не свойство.
Хеширование `o.type_str` без вызова создаёт новый bound-method на каждый
объект и раздувает память на гигабайты (OOM). Всегда `o.type_str()`.
"""

from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import os
import re
import resource
import sqlite3
import sys
import time

import osmium
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

# ----------------------------------------------------------------------------
# константы / словари
# ----------------------------------------------------------------------------

KZ_RELATION_ID = 214665          # r214665 = Казахстан (admin_level=2)

LIVING_AREA_PLACES = {'neighbourhood', 'suburb', 'quarter'}   # шаг 3
CITY_PLACES = {'city', 'town'}                                # шаг 2
ALL_PLACES = {'city', 'town', 'village', 'hamlet', 'suburb', 'quarter',
              'neighbourhood', 'locality', 'isolated_dwelling',
              'city_block', 'allotments', 'farm'}

# --- типы улиц: RU-тип стоит впереди, KK-тип — позади имени ---------------
# (regex, канонический тип, kind)
STREET_TYPE_RULES = [
    # --- микрорайоны / массивы / садовые товарищества ---
    (r'^(микрорайон|мкр-н|мкр\.|мкр|микро-н)\b', 'микрорайон', 'living_area'),
    (r'^(шағын аудан|ықшамаудан)\b', 'микрорайон', 'living_area'),
    (r'^(квартал|кв-л|кварт\.)\b', 'квартал', 'living_area'),
    (r'^(массив|м-н)\b', 'массив', 'living_area'),
    (r'^(пск)\b', 'ПСК', 'living_area'),
    (r'^(со)\b', 'СО', 'living_area'),
    (r'^(с/т|ст)\b', 'СТ', 'living_area'),
    (r'^(садовое товарищество)\b', 'СТ', 'living_area'),
    (r'^(поселок|посёлок|пос\.)\b', 'поселок', 'living_area'),
    (r'^(ауыл|ауылы)\b', 'ауыл', 'living_area'),
    (r'^(село|с\.)\b', 'село', 'living_area'),
    # --- улицы ---
    (r'^(улица|ул\.|ул)\b', 'улица', 'street'),
    (r'^(көшесі|кошеси|көш\.)', 'улица', 'street'),
    (r'^(проспект|пр-т\.?|пр\.|просп\.)\b', 'проспект', 'street'),
    (r'^(даңғылы|дангылы|д-лы)\b', 'проспект', 'street'),
    (r'^(переулок|пер\.|пер)\b', 'переулок', 'street'),
    (r'^(тұйық көше|туйык коше)\b', 'переулок', 'street'),
    (r'^(проезд|пр-д\.?|проезд\.)\b', 'проезд', 'street'),
    (r'^(бульвар|б-р|бул\.)\b', 'бульвар', 'street'),
    (r'^(шоссе|тас жол)\b', 'шоссе', 'street'),
    (r'^(площадь|пл\.|алаңы|аланы)\b', 'площадь', 'street'),
    (r'^(тупик)\b', 'тупик', 'street'),
    (r'^(набережная|наб\.|жағалау)\b', 'набережная', 'street'),
    (r'^(аллея|ал\.)\b', 'аллея', 'street'),
    (r'^(дорога|жолы)\b', 'дорога', 'street'),
    (r'^(тракт)\b', 'тракт', 'street'),
    (r'^(станция|ст\.|бекеті|бекет)\b', 'станция', 'street'),
    (r'^(разъезд)\b', 'разъезд', 'street'),
]
# KK-тип как СУФФИКС имени: «Абай көшесі», «Тұран даңғылы»
STREET_TYPE_SUFFIX = [
    (r'\s+(көшесі|кошеси)$', 'улица', 'street'),
    (r'\s+(даңғылы|дангылы)$', 'проспект', 'street'),
    (r'\s+(тұйық көше|туйык коше)$', 'переулок', 'street'),
    (r'\s+(алаңы|аланы)$', 'площадь', 'street'),
    (r'\s+(жағалауы|жагалауы)$', 'набережная', 'street'),
    (r'\s+(жолы)$', 'дорога', 'street'),
    (r'\s+(шағын ауданы|шагын ауданы)$', 'микрорайон', 'living_area'),
    (r'\s+(тоқсаны|токсаны)$', 'квартал', 'living_area'),
    (r'\s+(ауылы)$', 'ауыл', 'living_area'),
    (r'\s+(бекеті)$', 'станция', 'street'),
]
# тип в ЛЮБОМ месте (для «Правый восточный поселок», «5-й микрорайон»)
STREET_TYPE_ANYWHERE = [
    (r'\b(микрорайон|мкр-н|мкр)\b', 'микрорайон', 'living_area'),
    (r'\b(квартал|кв-л)\b', 'квартал', 'living_area'),
    (r'\b(массив)\b', 'массив', 'living_area'),
    (r'\b(поселок|посёлок)\b', 'поселок', 'living_area'),
    (r'\b(учётный квартал|учетный квартал)\b', 'квартал', 'living_area'),
]

RE_TYPE_A = [(re.compile(p, re.IGNORECASE), t, k) for p, t, k in STREET_TYPE_RULES]
RE_TYPE_SUF = [(re.compile(p, re.IGNORECASE), t, k) for p, t, k in STREET_TYPE_SUFFIX]
RE_TYPE_ANY = [(re.compile(p, re.IGNORECASE), t, k) for p, t, k in STREET_TYPE_ANYWHERE]

DISTRICT_STRIP = ('район', 'районы', 'ауданы', 'аудан', 'округ', 'округа')
DISTRICT_STRIP_RE = re.compile(
    r'(?i)(\bгородская администрация\b|\bсельская администрация\b|'
    r'\bқалалық әкімдігі\b|\bауылдық әкімдігі\b|\bауылдық округі\b|'
    r'\bсельский округ\b|\bсельсовет\b|'
    r'\bрайон\b|\bрайоны\b|\bауданы\b|\bаудан\b|\bокруг\b|\bокруга\b)')
CITY_STRIP_RE = re.compile(
    r'(?i)(\bқалалық әкімдігі\b|\bкенттік әкімдігі\b|\bауылдық әкімдігі\b|'
    r'\bгородская администрация\b|\bпоселковая администрация\b|'
    r'\bсельская администрация\b|\bауылдық округі\b|\bсельский округ\b|'
    r'\bсельсовет\b|\bқаласы\b|\bгород\b)')
PROVINCE_STRIP_RE = re.compile(r'(?i)(\bоблысы\b|\bобласть\b|\bоблыс\b)')

ALIAS_KEYS = ('old_name', 'alt_name', 'official_name', 'short_name',
              'int_name', 'name:ru', 'name:kk', 'name:en', 'name:prefix',
              'loc_name', 'name:old')

URBAN_NAME_HINTS = ('қалалық әкімдігі', 'кенттік әкімдігі',
                    'городская администрация', 'поселковая администрация')

SQL_DDL = """
PRAGMA page_size = 4096;
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;

CREATE TABLE provinces (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE cities (
    id INTEGER PRIMARY KEY,
    province_id INTEGER,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    FOREIGN KEY(province_id) REFERENCES provinces(id)
);

CREATE TABLE districts (
    id INTEGER PRIMARY KEY,
    city_id INTEGER,
    province_id INTEGER,
    name TEXT NOT NULL,         -- Без слова "район"
    name_norm TEXT NOT NULL
);

CREATE TABLE streets_and_areas (
    id INTEGER PRIMARY KEY,
    city_id INTEGER,
    district_id INTEGER,
    province_id INTEGER,
    entity_type TEXT NOT NULL,  -- 'street' или 'living_area'
    street_type TEXT,           -- 'улица', 'проспект', 'микрорайон'
    name_official TEXT NOT NULL,
    name_norm TEXT NOT NULL,     -- нормализованное ПОЛНОЕ имя с типом («12 микрорайон»)
    name_core_norm TEXT,         -- только ядро без типа («12») — для джойнов
    aliases TEXT,               -- JSON array: ["фурманова", "старое имя"]
    FOREIGN KEY(city_id) REFERENCES cities(id),
    FOREIGN KEY(district_id) REFERENCES districts(id)
);

CREATE VIRTUAL TABLE fts_address USING fts5(
    entity_id UNINDEXED,
    entity_type UNINDEXED,
    city_id UNINDEXED,
    district_id UNINDEXED,
    province_id UNINDEXED,
    street_type UNINDEXED,
    name_official UNINDEXED,
    search_text
);

-- ---- расширения схемы (не ломают ТЗ, нужны для полноты базы) ----
CREATE TABLE houses (
    id INTEGER PRIMARY KEY,
    street_id INTEGER,
    city_id INTEGER,
    district_id INTEGER,
    province_id INTEGER,
    housenumber TEXT,
    housenumber_norm TEXT,
    street_norm TEXT,            -- полное имя улицы с типом («улица абая»)
    street_core_norm TEXT,       -- ядро без типа («абая»)
    street_raw TEXT,
    place_norm TEXT,
    addr_city_norm TEXT,         -- addr:city для фоллбэка city_id
    place_id INTEGER,
    lat REAL,
    lon REAL,
    building TEXT,
    levels INTEGER
);

CREATE TABLE kato (
    id INTEGER PRIMARY KEY,
    code TEXT,
    name TEXT,
    name_norm TEXT,
    level TEXT,
    parent_code TEXT
);

CREATE TABLE kato_map (
    entity_table TEXT,
    entity_id INTEGER,
    kato_code TEXT,
    kato_name TEXT
);

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# ----------------------------------------------------------------------------
# нормализация и парсинг имён
# ----------------------------------------------------------------------------

_NORM_RE = re.compile(r'[^0-9a-zа-яёңғқұүһөәі]+', re.IGNORECASE)


def norm_text(s: str) -> str:
    """lower, ё->е, пунктуация -> пробел, схлопывание пробелов."""
    if not s:
        return ''
    s = s.lower().replace('ё', 'е').replace('№', ' ')
    s = _NORM_RE.sub(' ', s)
    return ' '.join(s.split())


def pick_name(tags) -> str:
    """Основное имя: name:ru -> name -> name:kk -> name:en."""
    for k in ('name:ru', 'name', 'name:kk', 'name:en', 'int_name'):
        v = tags.get(k)
        if v:
            return v.strip()
    return ''


def strip_words(name: str, rx: re.Pattern) -> str:
    out = rx.sub(' ', name or '')
    out = re.sub(r'[\s,;.\-]+$', '', out).strip(' ,;.-')
    return ' '.join(out.split())


def parse_street_name(name: str):
    """-> (street_type|None, core_name, kind 'street'|'living_area')."""
    nm = (name or '').strip()
    if not nm:
        return None, '', 'street'
    low = nm.lower()

    # 1) тип в начале имени
    for rx, stype, kind in RE_TYPE_A:
        m = rx.match(low)
        if m:
            core = nm[m.end():].strip(' .,;:-—')
            return stype, core or nm, kind

    # 2) тип как суффикс (казахская модель «Абай көшесі»)
    for rx, stype, kind in RE_TYPE_SUF:
        m = rx.search(low)
        if m:
            core = nm[:m.start()].strip(' .,;:-—')
            return stype, core or nm, kind

    # 3) тип встречается внутри («5-й микрорайон», «Правый поселок»)
    for rx, stype, kind in RE_TYPE_ANY:
        m = rx.search(low)
        if m:
            core = (nm[:m.start()] + ' ' + nm[m.end():]).strip(' .,;:-—')
            return stype, ' '.join(core.split()) or nm, kind

    return None, nm, 'street'


def collect_aliases(tags, canonical: str, core: str):
    """Список алиасов: old_name/alt_name/name:ru/name:kk/... + полная форма."""
    out, seen = [], set()
    can_n = norm_text(canonical)
    core_n = norm_text(core)
    seen.update({can_n, core_n})

    def push(v):
        if not v:
            return
        for part in re.split(r'[;,]', v):
            part = part.strip()
            n = norm_text(part)
            if len(n) < 2 or n in seen:
                continue
            seen.add(n)
            out.append(part)

    for k in ALIAS_KEYS:
        push(tags.get(k))
    nm = tags.get('name')
    if nm:
        push(nm)
        stype, c, _ = parse_street_name(nm)
        if stype and c:
            push(c)
    return out[:16]


def house_number_norm(v: str) -> str:
    return norm_text(v).replace(' ', '')


# ----------------------------------------------------------------------------
# утилиты
# ----------------------------------------------------------------------------

def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


class Timer:
    def __init__(self):
        self.t0 = time.time()
        self.last = self.t0

    def mark(self, msg):
        now = time.time()
        print(f'[{now - self.t0:7.1f}s | +{now - self.last:6.1f}s | '
              f'RSS {peak_rss_mb():6.0f} MB] {msg}', flush=True)
        self.last = now


def way_points(o):
    """Координаты вея (список (lon, lat))."""
    pts = []
    for nd in o.nodes:
        loc = nd.location
        if loc.valid():
            pts.append((loc.lon, loc.lat))
    return pts


def _ring_points(ring):
    pts = [(n.lon, n.lat) for n in ring]
    if len(pts) < 4:
        return None
    try:
        p = Polygon(pts)
    except Exception:
        return None
    if not p.is_valid:
        p = p.buffer(0)
    return None if p.is_empty else p


def area_geometry(a):
    """osmium.osm.Area -> shapely Polygon/MultiPolygon (outer - inner)."""
    outers, inners = [], []
    for oring in a.outer_rings():
        p = _ring_points(oring)
        if p is None:
            continue
        outers.append(p)
        try:
            for iring in a.inner_rings(oring):
                q = _ring_points(iring)
                if q is not None:
                    inners.append(q)
        except Exception:
            pass
    if not outers:
        return None
    try:
        g = unary_union(outers)
        if inners:
            g = g.difference(unary_union(inners))
    except Exception:
        g = unary_union(outers)
    if g.is_empty:
        return None
    if not g.is_valid:
        g = g.buffer(0)
    return g


class PolyIndex:
    """STRtree + prepared geometry: поиск содержащего полигона."""

    def __init__(self, items, label):
        self.label = label
        self.items = items
        geoms = [it['geom'] for it in items]
        for g in geoms:
            shapely.prepare(g)
        self.tree = STRtree(geoms) if geoms else None
        for it in items:
            it['area'] = it['geom'].area

    def __len__(self):
        return len(self.items)

    def smallest_containing(self, pt):
        """Индекс самого маленького полигона, содержащего точку (или None)."""
        if self.tree is None:
            return None
        best, best_area = None, None
        for i in self.tree.query(pt):
            it = self.items[i]
            if it['geom'].covers(pt):
                if best_area is None or it['area'] < best_area:
                    best, best_area = int(i), it['area']
        return best

    def largest_containing(self, pt):
        if self.tree is None:
            return None
        best, best_area = None, None
        for i in self.tree.query(pt):
            it = self.items[i]
            if it['geom'].covers(pt):
                if best_area is None or it['area'] > best_area:
                    best, best_area = int(i), it['area']
        return best


# ----------------------------------------------------------------------------
# ШАГ 1+2: границы и place-точки
# ----------------------------------------------------------------------------

def pass_admin(pbf, log):
    """with_areas(): KZ-полигон, provinces(4), districts(6), level8(8), place-ареалы."""
    provinces, districts, level8, place_areas = [], [], [], []
    kz_border = None
    n_area = 0

    fp = osmium.FileProcessor(pbf).with_areas()
    for a in fp:
        # with_areas() отдаёт и ноды/незамкнутые веи — берём только Area
        if not isinstance(a, osmium.osm.Area):
            continue
        tags = a.tags
        if not tags:
            continue

        admin = tags.get('boundary') == 'administrative'
        place = tags.get('place')
        if not admin and place not in ALL_PLACES:
            continue

        lvl = tags.get('admin_level')
        if admin and lvl not in ('2', '4', '6', '8', '9'):
            continue
        if not admin and place not in ALL_PLACES:
            continue

        n_area += 1
        geom = area_geometry(a)
        if geom is None:
            continue
        nm = pick_name(tags)
        rec = {
            'osm_id': a.orig_id(),
            'area_id': a.id,
            'osm_type': 'way' if a.from_way() else 'relation',
            'name': nm,
            'name_kk': tags.get('name:kk') or tags.get('name'),
            'name_ru': tags.get('name:ru'),
            'place': place,
            'admin_level': lvl,
            'iso': tags.get('ISO3166-2'),
            'geom': geom,
        }

        if admin and (lvl == '2'):
            if rec['osm_id'] == KZ_RELATION_ID or (rec['iso'] or '').startswith('KZ'):
                if kz_border is None or geom.area > kz_border.area:
                    kz_border = geom
                    log(f'pass1: KZ-полигон найден (r{rec["osm_id"]}, '
                        f'площадь {geom.area:.1f} кв.град)')
        elif admin and lvl == '4' and (rec['iso'] or '').startswith('KZ-'):
            provinces.append(rec)
        elif admin and lvl == '6':
            districts.append(rec)
        elif admin and lvl in ('8', '9'):
            level8.append(rec)
        elif place in ALL_PLACES:
            place_areas.append(rec)

    log(f'pass1: areas scanned={n_area}, provinces={len(provinces)}, '
        f'districts={len(districts)}, level8={len(level8)}, '
        f'place_areas={len(place_areas)}, kz_border={"ok" if kz_border else "none"}')

    if kz_border is None:
        # fallback: объединение KZ-провинций
        log('pass1: WARN KZ-полигон не найден, fallback = union(provinces)')
        kz_border = unary_union([p['geom'] for p in provinces]) if provinces else None
        if kz_border is None:
            raise SystemExit('Не удалось построить границу Казахстана')
    shapely.prepare(kz_border)
    return kz_border, provinces, districts, level8, place_areas


def pass_place_nodes(pbf, log):
    """Только ноды с place=* (для city/town)."""
    out = []
    try:
        entities = osmium.osm.NODE
    except AttributeError:                                   # старые версии
        entities = osmium.osm.NODE
    fp = osmium.FileProcessor(pbf, entities=entities)
    for o in fp:
        tags = o.tags
        if not tags:
            continue
        place = tags.get('place')
        if place not in ALL_PLACES:
            continue
        nm = pick_name(tags)
        if not nm:
            continue
        pop = tags.get('population')
        try:
            pop = int(re.sub(r'\D', '', pop)) if pop else None
        except ValueError:
            pop = None
        out.append({
            'osm_id': o.id,
            'name': nm,
            'name_kk': tags.get('name:kk'),
            'name_ru': tags.get('name:ru'),
            'place': place,
            'population': pop,
            'lon': o.location.lon,
            'lat': o.location.lat,
            'geom': Point(o.location.lon, o.location.lat),
        })
    log(f'pass2: place-ноды={len(out)}')
    return out


# ----------------------------------------------------------------------------
# сборка иерархии
# ----------------------------------------------------------------------------

def build_hierarchy(kz_border, provinces, districts, level8, place_areas,
                    place_nodes, level8_mode, log):
    # ---------- provinces ----------
    prov_items = []
    for p in provinces:
        rep = p['geom'].representative_point()
        if not kz_border.covers(rep):
            continue
        name = strip_words(p['name_ru'] or p['name'], PROVINCE_STRIP_RE) or p['name']
        prov_items.append({
            'geom': p['geom'], 'osm_id': p['osm_id'], 'admin_level': '4',
            'name': clean_province_name(p), 'name_norm': norm_text(clean_province_name(p)),
            'name_kk': p['name_kk'], 'iso': p['iso'], 'place': p['place'],
            'name_ru': p['name_ru'],
        })
    prov_items.sort(key=lambda x: x['name'])
    prov_idx = PolyIndex(prov_items, 'provinces')

    # ---------- districts (level 6) ----------
    dist_items = []
    for d in districts:
        rep = d['geom'].representative_point()
        if not kz_border.covers(rep):
            continue
        raw = d['name_ru'] or d['name']
        name = clean_district_name(d)
        dist_items.append({
            'geom': d['geom'], 'osm_id': d['osm_id'], 'admin_level': '6',
            'name': name, 'name_norm': norm_text(name),
            'name_raw': d['name'], 'name_ru': d['name_ru'],
            'province': prov_idx.smallest_containing(rep),
        })
    dist_items = [d for d in dist_items if d['name']]
    dist_idx = PolyIndex(dist_items, 'districts')

    # ---------- cities ----------
    # level-8 акиматы (нужны и как кандидаты в города, и как точные полигоны)
    l8_items = []
    for l8 in level8:
        rep = l8['geom'].representative_point()
        if not kz_border.covers(rep):
            continue
        raw = l8['name'] or l8['name_ru'] or ''
        low = raw.lower()
        urban = any(h in low for h in URBAN_NAME_HINTS) or l8['place'] in CITY_PLACES
        if not urban and level8_mode != 'all':
            continue
        l8_items.append({
            'geom': l8['geom'], 'osm_id': l8['osm_id'], 'admin_level': '8',
            'name': clean_city_name(l8['name_ru'] or l8['name'] or raw),
            'name_kk': l8['name_kk'], 'name_ru': l8['name_ru'],
            'place': l8['place'] or ('town' if 'кенттік' in low else 'city'),
            'urban': urban, 'rep': rep, 'name_raw': raw,
        })
    for it in l8_items:
        it['name_norm'] = norm_text(it['name'])

    admin_pool = prov_items + dist_items + l8_items   # поиск полигона города
    admin_idx = PolyIndex(admin_pool, 'admin_pool')

    cities = []
    used = set()                                  # уже использованные полигоны

    place_nodes.sort(key=lambda x: (-(x['population'] or 0), x['name']))

    def add_city(name, name_kk, name_ru, place, geom, source, population=None):
        rep = geom.representative_point() if geom.geom_type != 'Point' else geom
        nrm = norm_text(name)
        pidx = prov_idx.smallest_containing(rep)
        # антидубль: то же имя в той же области и геометрии вложены друг в друга
        for c in cities:
            if c['name_norm'] != nrm or c['province'] != pidx:
                continue
            try:
                if c['geom'].covers(rep) or geom.covers(
                        c['geom'].representative_point()):
                    return False
            except Exception:
                return False
        cities.append({
            'geom': geom, 'name': name, 'name_norm': nrm,
            'name_kk': name_kk, 'name_ru': name_ru,
            'place': place, 'source': source, 'population': population,
            'province': pidx,
            'district': dist_idx.smallest_containing(rep),
        })
        return True

    for pn in place_nodes:
        if pn['place'] not in CITY_PLACES:
            continue
        pt = pn['geom']
        if not kz_border.covers(pt):
            continue
        # полигон города ищем только по СОВПАДЕНИЮ ИМЕНИ и уровню 8, затем 4
        # (level 6 — это районы, их брать нельзя: «Абай ауданы» != город Абай)
        nrm = norm_text(pn['name'])
        best = None
        for want_level in ('8', '4'):
            cands = []
            for i in admin_idx.tree.query(pt):
                it = admin_pool[i]
                if (it['admin_level'] == want_level and it['name_norm'] == nrm
                        and it['geom'].covers(pt)):
                    cands.append((it['area'], i))
            if cands:
                best = admin_pool[min(cands)[1]]
                break
        if best is not None:
            key = ('poly', best['osm_id'], best['admin_level'])
            if key in used:
                continue
            used.add(key)
            geom, src = best['geom'], 'place+admin' + best['admin_level']
        else:
            geom, src = pt.buffer(0.02), 'place'
        add_city(clean_city_name(pn['name_ru'] or pn['name']), pn['name_kk'],
                 pn['name_ru'], pn['place'], geom, src, pn['population'])

    # level8-акиматы: городские/поселковые -> city
    for l8 in l8_items:
        key = ('poly', l8['osm_id'], '8')
        if key in used:
            continue
        used.add(key)
        add_city(l8['name'], l8['name_kk'], l8['name_ru'], l8['place'],
                 l8['geom'], 'admin8')

    # place-ареалы (place=city|town как полигоны), если такого имени ещё нет
    have = {c['name_norm'] for c in cities}
    for pa in place_areas:
        if pa['place'] not in CITY_PLACES:
            continue
        rep = pa['geom'].representative_point()
        if not kz_border.covers(rep):
            continue
        if norm_text(pa['name']) in have:
            continue
        add_city(clean_city_name(pa['name_ru'] or pa['name']), pa['name_kk'],
                 pa['name_ru'], pa['place'], pa['geom'], 'place_area')
        have.add(norm_text(pa['name']))

    cities = [c for c in cities if c['name']]
    cities.sort(key=lambda x: (-(x['population'] or 0), x['name']))
    city_idx = PolyIndex(cities, 'cities')

    # ---------- привязка district -> city (только если район целиком в городе) ----
    for d in dist_items:
        c = city_idx.smallest_containing(d['geom'].representative_point())
        if c is None:
            d['city'] = None
            continue
        try:
            inter = d['geom'].intersection(cities[c]['geom']).area
            ratio = inter / d['geom'].area if d['geom'].area else 0.0
        except Exception:
            ratio = 0.0
        d['city'] = c if ratio > 0.9 else None

    log(f'hierarchy: provinces={len(prov_items)}, cities={len(cities)}, '
        f'districts={len(dist_items)} '
        f'(в городах={sum(1 for d in dist_items if d["city"] is not None)})')
    return prov_items, prov_idx, cities, city_idx, dist_items, dist_idx


# ----------------------------------------------------------------------------
# SQLite
# ----------------------------------------------------------------------------

def clean_province_name(rec) -> str:
    """'Актюбинская область' / для городов — просто 'Алматы', 'Астана', 'Шымкент'."""
    name_ru = (rec.get('name_ru') or '').strip()
    name = (rec.get('name') or '').strip()
    if rec.get('place') == 'city':
        return clean_city_name(name_ru or name)
    if name_ru:
        return ' '.join(name_ru.split())
    if name:
        # каз. «Ақтөбе облысы» -> «Ақтөбе область»
        return ' '.join(PROVINCE_STRIP_RE.sub(' область ', name).split())
    return ''


CITY_PREFIX_RE = re.compile(
    r'(?i)^\s*(г\.|гор\.|город|пос\.|посёлок|поселок|пгт|село|с\.|станица|ст-ца|'
    r'аул|аул\.|ауылы|қаласы|қала|кент)\s+', re.UNICODE)


def clean_city_name(raw: str) -> str:
    """Чистое имя НП: без «г.», «город», «пос.», «село»; из «А/Б» — только русская часть."""
    s = (raw or '').strip()
    if not s:
        return ''
    if '/' in s:                       # «город Актобе/Ақтөбе қаласы» -> «город Актобе»
        s = s.split('/')[0]
    s = CITY_PREFIX_RE.sub('', s)
    s = CITY_STRIP_RE.sub(' ', s)      # «қалалық әкімдігі», «ауылдық округі» и пр.
    s = s.strip(' .,;:-—')
    return ' '.join(s.split())


def clean_district_name(rec) -> str:
    """Без «район»/«ауданы»: «Илийский район» -> «Илийский»."""
    raw = (rec.get('name_ru') or rec.get('name') or '').strip()
    if '/' in raw:
        raw = raw.split('/')[0]
    name = strip_words(raw, DISTRICT_STRIP_RE)
    return name or ' '.join(raw.split())


def open_db(path):
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.executescript(SQL_DDL)
    return conn


def write_admin(conn, prov_items, cities, dist_items):
    """provinces / cities / districts -> SQLite, возвращает смещения id."""
    rows = [(i + 1, p['name']) for i, p in enumerate(prov_items)]
    conn.executemany('INSERT INTO provinces(id, name) VALUES (?,?)', rows)

    P = len(prov_items)
    rows = []
    for i, c in enumerate(cities):
        pid = (c['province'] + 1) if c['province'] is not None else None
        rows.append((P + i + 1, pid, c['name'], c['name_norm']))
    conn.executemany('INSERT INTO cities(id, province_id, name, name_norm) '
                     'VALUES (?,?,?,?)', rows)

    C = P + len(cities)
    rows = []
    for i, d in enumerate(dist_items):
        pid = (d['province'] + 1) if d['province'] is not None else None
        cid = (P + d['city'] + 1) if d['city'] is not None else None
        rows.append((C + i + 1, cid, pid, d['name'], d['name_norm']))
    conn.executemany('INSERT INTO districts(id, city_id, province_id, name, name_norm) '
                     'VALUES (?,?,?,?,?)', rows)
    conn.commit()
    return P, C


# ----------------------------------------------------------------------------
# ШАГ 3: улицы, микрорайоны, дома
# ----------------------------------------------------------------------------

STREET_LIKE_HIGHWAY_SKIP = {'street_lamp', 'traffic_signals', 'crossing', 'bus_stop',
                            'milestone', 'speed_camera', 'stop', 'give_way',
                            'elevator', 'platform', 'traffic_mirror', 'toll_gantry',
                            'street_cabinet', 'power_pole', 'motorway_junction',
                            'turning_circle', 'turning_loop', 'mini_roundabout',
                            'passing_place', 'rest_area', 'services', 'emergency_bay'}


def pass_streets(conn, pbf, kz_border, prov_idx, city_idx, dist_idx, offsets,
                 with_houses, log):
    P, C = offsets
    agg = {}                       # (city_i, kind, name_norm) -> запись
    house_buf = []
    n_highway = n_living = n_house = n_skip_out = n_addr_place = 0
    BATCH = 20000

    def flush_houses():
        nonlocal house_buf
        if house_buf:
            conn.executemany(
                'INSERT INTO houses(street_id, city_id, district_id, province_id, '
                'housenumber, housenumber_norm, street_norm, street_core_norm, '
                'street_raw, place_norm, addr_city_norm, lat, lon, building, levels) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', house_buf)
            house_buf = []

    fp = osmium.FileProcessor(pbf).with_locations()
    for o in fp:
        tags = o.tags
        if not tags:
            continue
        ot = o.type_str()

        # ---------------- дома ----------------
        if with_houses and 'addr:housenumber' in tags:
            pt = None
            if ot == 'n':
                loc = o.location
                if loc.valid():
                    pt = Point(loc.lon, loc.lat)
            elif ot == 'w':
                pts = way_points(o)
                if len(pts) >= 2:
                    try:
                        pt = Polygon(pts).representative_point() \
                            if len(pts) > 3 and pts[0] == pts[-1] \
                            else LineString(pts).interpolate(0.5, normalized=True)
                    except Exception:
                        x = sum(p[0] for p in pts) / len(pts)
                        y = sum(p[1] for p in pts) / len(pts)
                        pt = Point(x, y)
            if pt is not None:
                if kz_border.covers(pt):
                    ci = city_idx.smallest_containing(pt)
                    di = dist_idx.smallest_containing(pt)
                    pi = prov_idx.smallest_containing(pt)
                    lv = tags.get('building:levels')
                    try:
                        lv = int(re.sub(r'\D', '', lv)[:3]) if lv else None
                    except (ValueError, IndexError):
                        lv = None
                    s_raw = tags.get('addr:street')
                    p_raw = tags.get('addr:place')
                    house_buf.append((
                        None,
                        (P + ci + 1) if ci is not None else None,
                        (C + di + 1) if di is not None else None,
                        (pi + 1) if pi is not None else None,
                        tags.get('addr:housenumber'),
                        house_number_norm(tags.get('addr:housenumber') or ''),
                        norm_text(s_raw) if s_raw else None,
                        street_core_norm(s_raw)[1],
                        s_raw,
                        street_core_norm(p_raw)[1],
                        norm_text(tags.get('addr:city') or '') or None,
                        round(pt.y, 7), round(pt.x, 7),
                        tags.get('building'), lv))
                    n_house += 1
                    if len(house_buf) >= BATCH:
                        flush_houses()
                    # addr:place -> категория living_area (ШАГ 2.5 ТЗ)
                    addr_place = tags.get('addr:place')
                    if addr_place and len(addr_place.strip()) >= 3:
                        n_addr_place += 1
                        _accum(agg, tags, addr_place, 'living_area', pt, 0.0,
                               city_idx, dist_idx, prov_idx)
                else:
                    n_skip_out += 1
            continue

        # ---------------- именованные дороги ----------------
        if ot == 'w':
            hw = tags.get('highway')
            if hw is None or hw in STREET_LIKE_HIGHWAY_SKIP:
                continue
            name = tags.get('name')
            if not name:
                continue
            pts = way_points(o)
            if len(pts) < 2:
                continue
            try:
                line = LineString(pts)
            except Exception:
                continue
            rep = line.interpolate(0.5, normalized=True)
            if not kz_border.covers(rep):
                n_skip_out += 1
                continue
            n_highway += 1
            _accum(agg, tags, name, 'street', rep, line.length,
                   city_idx, dist_idx, prov_idx)
            continue

        # ---------------- микрорайоны / кварталы ----------------
        place = tags.get('place')
        if place in LIVING_AREA_PLACES:
            name = tags.get('name')
            if not name:
                continue
            if ot == 'n':
                loc = o.location
                if not loc.valid():
                    continue
                pt = Point(loc.lon, loc.lat)
                length = 0.0
            elif ot == 'w':
                pts = way_points(o)
                if not pts:
                    continue
                try:
                    geom = Polygon(pts) if pts[0] == pts[-1] else LineString(pts)
                    pt = geom.representative_point()
                    length = 0.0
                except Exception:
                    x = sum(p[0] for p in pts) / len(pts)
                    y = sum(p[1] for p in pts) / len(pts)
                    pt = Point(x, y)
                    length = 0.0
            else:
                continue
            if not kz_border.covers(pt):
                n_skip_out += 1
                continue
            n_living += 1
            _accum(agg, tags, name, 'living_area', pt, length,
                   city_idx, dist_idx, prov_idx)

    flush_houses()
    log(f'pass3: highway-сегментов={n_highway}, living_area={n_living}, '
        f'домов={n_house}, из них с addr:place={n_addr_place}, '
        f'отсечено вне KZ={n_skip_out}, агрегированных записей={len(agg)}')

    # ---------------- запись streets_and_areas + FTS ----------------
    rows, fts = [], []
    for (ci, kind, nrm), rec in agg.items():
        stype = rec['street_type'].most_common(1)[0][0] if rec['street_type'] else None
        official = rec['official'].most_common(1)[0][0]
        aliases = sorted(rec['aliases'])
        didx = rec['district'].most_common(1)[0][0] if rec['district'] else None
        pidx = rec['province'].most_common(1)[0][0] if rec['province'] else None
        cid = (P + ci + 1) if ci is not None else None
        did = (C + didx + 1) if didx is not None else None
        pid = (pidx + 1) if pidx is not None else None
        sid = len(rows) + 1
        full_norm = norm_text(official)          # ПОЛНОЕ имя с типом
        rows.append((sid, cid, did, pid, kind, stype, official, full_norm, nrm,
                     json.dumps(aliases, ensure_ascii=False)))
        search = ' '.join([full_norm, nrm, norm_text(stype or '')] +
                          [norm_text(a) for a in aliases])
        fts.append((sid, kind, cid, did, pid, stype, official,
                    ' '.join(dict.fromkeys(search.split()))))

    conn.executemany(
        'INSERT INTO streets_and_areas(id, city_id, district_id, province_id, '
        'entity_type, street_type, name_official, name_norm, name_core_norm, '
        'aliases) VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
    conn.executemany(
        'INSERT INTO fts_address(entity_id, entity_type, city_id, district_id, '
        'province_id, street_type, name_official, search_text) '
        'VALUES (?,?,?,?,?,?,?,?)', fts)
    conn.commit()
    log(f'pass3: записано streets_and_areas={len(rows)}')
    return len(rows), n_house


def _accum(agg, tags, name, kind, rep_pt, length,
           city_idx, dist_idx, prov_idx):
    stype, core, kind_by_name = parse_street_name(name)
    if kind == 'living_area':
        kind = 'living_area'
    elif kind_by_name == 'living_area':
        kind = 'living_area'

    ci = city_idx.smallest_containing(rep_pt)
    di = dist_idx.smallest_containing(rep_pt)
    pi = prov_idx.smallest_containing(rep_pt)

    core_n = norm_text(core) or norm_text(name)
    key = (ci, kind, core_n)
    rec = agg.get(key)
    if rec is None:
        rec = agg[key] = {
            'official': collections.Counter(),
            'street_type': collections.Counter(),
            'aliases': set(),
            'district': collections.Counter(),
            'province': collections.Counter(),
            'length': 0.0,
            'segs': 0,
        }
    rec['official'][name.strip()] += 1
    if stype:
        rec['street_type'][stype] += 1
    rec['segs'] += 1
    rec['length'] += length
    if di is not None:
        rec['district'][di] += length or 1.0
    if pi is not None:
        rec['province'][pi] += length or 1.0
    for al in collect_aliases(tags, name, core):
        rec['aliases'].add(al)


# ----------------------------------------------------------------------------
# КАТО (опционально)
# ----------------------------------------------------------------------------

KATO_CODE_KEYS = ('kato', 'code', 'kod', 'katocode', 'id')
KATO_NAME_KEYS = ('name', 'name_ru', 'namerus', 'название', 'наименование')
KATO_LEVEL_KEYS = ('level', 'lvl', 'type', 'уровень', 'te')
KATO_PARENT_KEYS = ('parent', 'parent_code', 'parentkato', 'te')


def load_kato(path):
    """JSON (плоский/вложенный children) или CSV -> list[dict]."""
    rows = []

    def walk(node, parent=None):
        if isinstance(node, list):
            for x in node:
                walk(x, parent)
            return
        if not isinstance(node, dict):
            return
        low = {str(k).lower(): v for k, v in node.items()}
        code = next((str(low[k]) for k in KATO_CODE_KEYS if low.get(k) is not None), None)
        name = next((str(low[k]) for k in KATO_NAME_KEYS if low.get(k)), None)
        lvl = next((str(low[k]) for k in KATO_LEVEL_KEYS if low.get(k) is not None), None)
        par = next((str(low[k]) for k in KATO_PARENT_KEYS if low.get(k) is not None),
                   parent)
        if name:
            rows.append({'code': code, 'name': name, 'level': lvl, 'parent': par})
            parent = code
        for key in ('children', 'items', 'districts', 'cities', 'regions', 'data'):
            if key in low:
                walk(low[key], parent)

    if path.lower().endswith('.json'):
        with open(path, encoding='utf-8') as f:
            walk(json.load(f))
    else:
        with open(path, encoding='utf-8-sig', newline='') as f:
            sample = f.read(4096)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
            rd = csv.DictReader(f, dialect=dialect)
            for raw in rd:
                low = {(k or '').lower().strip(): v for k, v in raw.items()}
                code = next((low[k] for k in KATO_CODE_KEYS if low.get(k)), None)
                name = next((low[k] for k in KATO_NAME_KEYS if low.get(k)), None)
                lvl = next((low[k] for k in KATO_LEVEL_KEYS if low.get(k)), None)
                par = next((low[k] for k in KATO_PARENT_KEYS if low.get(k)), None)
                if name:
                    rows.append({'code': code, 'name': name.strip(),
                                 'level': lvl, 'parent': par})
    return rows


def write_kato(conn, rows):
    conn.executemany(
        'INSERT INTO kato(code, name, name_norm, level, parent_code) VALUES (?,?,?,?,?)',
        [(r['code'], r['name'], norm_text(r['name']), r['level'], r['parent'])
         for r in rows])
    conn.commit()


def match_kato(conn, prov_items, cities, dist_items):
    """Сопоставление КАТО -> provinces/cities/districts по нормализованному имени."""
    kato = conn.execute('SELECT code, name_norm, level FROM kato').fetchall()
    by_norm = collections.defaultdict(list)
    for code, nn, lvl in kato:
        by_norm[nn].append((code, lvl))

    P, C = len(prov_items), len(cities)
    pairs = []
    for i, p in enumerate(prov_items):
        for code, lvl in by_norm.get(p['name_norm'], [])[:1]:
            pairs.append(('provinces', i + 1, code, p['name']))
    for i, c in enumerate(cities):
        for code, lvl in by_norm.get(c['name_norm'], [])[:1]:
            pairs.append(('cities', P + i + 1, code, c['name']))
    for i, d in enumerate(dist_items):
        for code, lvl in by_norm.get(d['name_norm'], [])[:1]:
            pairs.append(('districts', P + C + i + 1, code, d['name']))
    conn.executemany('INSERT INTO kato_map(entity_table, entity_id, kato_code, kato_name) '
                     'VALUES (?,?,?,?)', pairs)
    conn.commit()
    return len(pairs)


# ----------------------------------------------------------------------------
# отчёт + FTS-тест
# ----------------------------------------------------------------------------

def create_indexes(conn):
    # временные индексы, нужные только для привязки, — удаляем, иначе БД пухнет
    for tmp in ('idx_houses_city_street', 'idx_houses_city_core',
                'idx_houses_city_place', 'idx_houses_addr_city',
                'idx_houses_district_core', 'idx_houses_street_norm'):
        conn.execute(f'DROP INDEX IF EXISTS {tmp}')
    conn.commit()

    stmts = [
        'CREATE INDEX idx_cities_province ON cities(province_id)',
        'CREATE INDEX idx_cities_norm ON cities(name_norm)',
        'CREATE INDEX idx_districts_city ON districts(city_id)',
        'CREATE INDEX idx_districts_province ON districts(province_id)',
        'CREATE INDEX idx_districts_norm ON districts(name_norm)',
        'CREATE INDEX idx_sa_city_norm ON streets_and_areas(city_id, name_norm)',
        'CREATE INDEX idx_sa_norm ON streets_and_areas(name_norm)',
        'CREATE INDEX idx_sa_district ON streets_and_areas(district_id)',
        'CREATE INDEX idx_sa_province ON streets_and_areas(province_id)',
        'CREATE INDEX idx_sa_type ON streets_and_areas(entity_type)',
    ]
    if conn.execute("SELECT name FROM sqlite_master WHERE name='houses'").fetchone():
        stmts += [
            'CREATE INDEX idx_houses_city ON houses(city_id)',
            'CREATE INDEX idx_houses_district ON houses(district_id)',
            'CREATE INDEX idx_houses_street ON houses(street_id)',
            'CREATE INDEX idx_houses_norm ON houses(housenumber_norm)',
        ]
    for s in stmts:
        conn.execute(s.replace('CREATE INDEX ', 'CREATE INDEX IF NOT EXISTS '))
    conn.commit()


def street_core_norm(raw: str):
    """addr:street -> (raw, name_norm ядра, street_type, kind).

    «улица Абая» -> («улица Абая», «абая», улица, street)
    «Аксай 3а микрорайон» -> («…», «аксай 3а», микрорайон, living_area)
    """
    raw = (raw or '').strip()
    if not raw:
        return None, None, None, 'street'
    stype, core, kind = parse_street_name(raw)
    nrm = norm_text(core) or norm_text(raw)
    return raw, (nrm or None), stype, kind


def resolve_house_streets(conn, log):
    """Привязка домов к улицам/жилмассивам: сначала по полному имени, затем по ядру."""
    for sql in (
        'CREATE INDEX IF NOT EXISTS idx_sa_city_norm '
        'ON streets_and_areas(city_id, name_norm)',
        'CREATE INDEX IF NOT EXISTS idx_sa_city_core '
        'ON streets_and_areas(city_id, name_core_norm)',
        'CREATE INDEX IF NOT EXISTS idx_houses_city_street '
        'ON houses(city_id, street_norm)',
        'CREATE INDEX IF NOT EXISTS idx_houses_city_core '
        'ON houses(city_id, street_core_norm)',
        'CREATE INDEX IF NOT EXISTS idx_houses_city_place '
        'ON houses(city_id, place_norm)',
        'CREATE INDEX IF NOT EXISTS idx_cities_norm ON cities(name_norm)',
    ):
        conn.execute(sql)

    # 1) точное совпадение полного имени («улица абая» == «улица абая»)
    conn.execute("""
        UPDATE houses SET street_id = (
            SELECT s.id FROM streets_and_areas s
            WHERE s.city_id IS houses.city_id
              AND s.entity_type = 'street'
              AND s.name_norm = houses.street_norm
            LIMIT 1)
        WHERE street_norm IS NOT NULL AND street_id IS NULL
    """)
    # 2) фоллбэк по ядру без типа («улица абая» ~ «абай көшесі»)
    conn.execute("""
        UPDATE houses SET street_id = (
            SELECT s.id FROM streets_and_areas s
            WHERE s.city_id IS houses.city_id
              AND s.entity_type = 'street'
              AND s.name_core_norm = houses.street_core_norm
            LIMIT 1)
        WHERE street_core_norm IS NOT NULL AND street_id IS NULL
    """)
    # 3) жилмассивы: полное имя, затем ядро
    conn.execute("""
        UPDATE houses SET place_id = (
            SELECT s.id FROM streets_and_areas s
            WHERE s.city_id IS houses.city_id
              AND s.entity_type = 'living_area'
              AND (s.name_norm = houses.place_norm
                   OR s.name_core_norm = houses.place_norm)
            LIMIT 1)
        WHERE place_norm IS NOT NULL AND place_id IS NULL
    """)
    conn.commit()
    tot = conn.execute('SELECT COUNT(*) FROM houses WHERE street_norm IS NOT NULL').fetchone()[0]
    n = conn.execute('SELECT COUNT(*) FROM houses WHERE street_id IS NOT NULL').fetchone()[0]
    if tot:
        log(f'houses->streets: привязано {n} из {tot} ({100.0 * n / tot:.1f}%)')
    else:
        log('houses->streets: нет домов с улицей')
    return n, tot


def fill_orphan_city_ids(conn, log):
    """Фоллбэк city_id для «сиротских» улиц/домов.

    Источники по убыванию надёжности (везде проверяется, что город из ТОЙ ЖЕ
    области — иначе битый addr:city утащит улицу в другой регион):
      1) домам: тег addr:city;
      2) улицам: мода city_id их собственных домов;
      3) улицам: city_id района, если он известен;
      4) улицам: дома того же района с тем же ядром имени;
      5) домам: city_id привязанной улицы.
    """
    def stat(tbl):
        total = conn.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
        null = conn.execute(f'SELECT COUNT(*) FROM {tbl} WHERE city_id IS NULL').fetchone()[0]
        return total, null

    def pick(counter, province_id):
        """Город с максимальным числом голосов, но только из нужной области."""
        for city_id, _n in counter.most_common():
            cp = city_prov.get(city_id)
            if province_id is None or cp is None or cp == province_id:
                return city_id
        return None

    s_tot, s_null0 = stat('streets_and_areas')
    h_tot, h_null0 = stat('houses')

    city_prov = {cid: pid for cid, pid in
                 conn.execute('SELECT id, province_id FROM cities')}
    city_by_norm = collections.defaultdict(list)
    for cid, nn, pid in conn.execute('SELECT id, name_norm, province_id FROM cities'):
        city_by_norm[nn].append((cid, pid))

    # ---- 1) дома <- addr:city ----
    rows = conn.execute('SELECT id, addr_city_norm, province_id FROM houses '
                        'WHERE city_id IS NULL AND addr_city_norm IS NOT NULL').fetchall()
    upd = []
    for hid, nn, pid in rows:
        cands = city_by_norm.get(nn)
        if not cands:
            continue
        same = [c for c in cands if pid is not None and c[1] == pid]
        if same:
            upd.append((same[0][0], hid))
        elif pid is None:                      # область дома неизвестна — берём любой
            upd.append((cands[0][0], hid))
        # иначе: addr:city из чужой области = битые данные, оставляем NULL
    conn.executemany('UPDATE houses SET city_id = ? WHERE id = ?', upd)
    conn.commit()
    n_addr_city = len(upd)

    # ---- 2) улицы <- мода city_id их домов ----
    street_votes = collections.defaultdict(collections.Counter)
    for sid, cid, n in conn.execute(
            'SELECT street_id, city_id, COUNT(*) FROM houses '
            'WHERE street_id IS NOT NULL AND city_id IS NOT NULL '
            'GROUP BY street_id, city_id'):
        street_votes[sid][cid] = n

    # ---- 4) дома района с тем же ядром имени ----
    district_core_votes = collections.defaultdict(collections.Counter)
    for did, core, cid, n in conn.execute(
            'SELECT district_id, street_core_norm, city_id, COUNT(*) FROM houses '
            'WHERE district_id IS NOT NULL AND street_core_norm IS NOT NULL '
            '  AND city_id IS NOT NULL GROUP BY district_id, street_core_norm, city_id'):
        district_core_votes[(did, core)][cid] = n

    district_city = {did: cid for did, cid in
                     conn.execute('SELECT id, city_id FROM districts '
                                  'WHERE city_id IS NOT NULL')}

    streets = conn.execute(
        'SELECT id, province_id, district_id, name_core_norm '
        'FROM streets_and_areas WHERE city_id IS NULL').fetchall()
    upd = []
    for sid, pid, did, core in streets:
        cand = pick(street_votes.get(sid, {}), pid) if sid in street_votes else None
        if cand is None and did in district_city:
            c = district_city[did]
            cp = city_prov.get(c)
            if pid is None or cp is None or cp == pid:
                cand = c
        if cand is None and core:
            votes = district_core_votes.get((did, core))
            if votes:
                cand = pick(votes, pid)
        if cand is not None:
            upd.append((cand, sid))
    conn.executemany('UPDATE streets_and_areas SET city_id = ? WHERE id = ?', upd)
    conn.commit()

    # ---- 5) дома <- привязанная улица ----
    conn.execute("""
        UPDATE houses SET city_id = (
            SELECT s.city_id FROM streets_and_areas s
            JOIN cities c ON c.id = s.city_id
            WHERE s.id = houses.street_id
              AND (houses.province_id IS NULL OR c.province_id IS NULL
                   OR c.province_id = houses.province_id))
        WHERE city_id IS NULL AND street_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM streets_and_areas s JOIN cities c ON c.id = s.city_id
            WHERE s.id = houses.street_id
              AND (houses.province_id IS NULL OR c.province_id IS NULL
                   OR c.province_id = houses.province_id))
    """)
    conn.commit()

    s_tot, s_null1 = stat('streets_and_areas')
    h_tot, h_null1 = stat('houses')
    log(f'city_id-фоллбэк: дома {h_tot - h_null1}/{h_tot} '
        f'({100.0 * (h_tot - h_null1) / max(1, h_tot):.1f}%), '
        f'в т.ч. по addr:city={n_addr_city}, было NULL {h_null0}; '
        f'улицы {s_tot - s_null1}/{s_tot} '
        f'({100.0 * (s_tot - s_null1) / max(1, s_tot):.1f}%), было NULL {s_null0}')


def promote_address_entities(conn, log):
    """Досоздаёт улицы/микрорайоны из addr:street, которых нет среди OSM-объектов.

    Иначе адреса вида «Аксай 3а микрорайон» (есть только в addr:street, но нет ни
    highway, ни place-объекта) выпадают из справочника и из FTS.
    """
    rows = conn.execute("""
        SELECT city_id, district_id, province_id, street_raw, street_norm, COUNT(*)
        FROM houses
        WHERE street_id IS NULL AND street_norm IS NOT NULL
          AND length(street_norm) >= 3
          AND street_norm GLOB '*[а-яА-Яa-zA-Z]*'
        GROUP BY city_id, street_norm
    """).fetchall()
    if not rows:
        return 0

    cur_id = conn.execute('SELECT COALESCE(MAX(id),0) FROM streets_and_areas').fetchone()[0]
    new_rows, fts_rows, links = [], [], []
    for city_id, district_id, province_id, raw, nrm, _cnt in rows:
        name = (raw or nrm).strip()
        stype, core, kind = parse_street_name(name)
        cur_id += 1
        new_rows.append((cur_id, city_id, district_id, province_id, kind, stype,
                         name, norm_text(name), nrm, '[]'))
        links.append((cur_id, city_id, nrm))
        search = ' '.join([norm_text(name), nrm, norm_text(stype or ''),
                           norm_text(core)])
        fts_rows.append((cur_id, kind, city_id, district_id, province_id, stype,
                         name, ' '.join(dict.fromkeys(search.split()))))

    conn.executemany(
        'INSERT INTO streets_and_areas(id, city_id, district_id, province_id, '
        'entity_type, street_type, name_official, name_norm, name_core_norm, '
        'aliases) VALUES (?,?,?,?,?,?,?,?,?,?)', new_rows)
    conn.executemany(
        'UPDATE houses SET street_id = ? WHERE city_id IS ? AND street_norm = ? '
        'AND street_id IS NULL', links)
    conn.commit()
    conn.executemany(
        'INSERT INTO fts_address(entity_id, entity_type, city_id, district_id, '
        'province_id, street_type, name_official, search_text) '
        'VALUES (?,?,?,?,?,?,?,?)', fts_rows)
    conn.commit()
    log(f'pass3.5: досоздано сущностей из addr:street={len(new_rows)}')
    return len(new_rows)


def fts_test(conn, query='аксай 3а', limit=8):
    """Тестовый FTS-поиск: найденные сущности + их родители."""
    q = norm_text(query)
    tokens = q.split()
    variants = [f'"{q}"']
    if tokens:
        variants.append(' AND '.join(
            (t + '*') if i == len(tokens) - 1 else t for i, t in enumerate(tokens)))
        variants.append(tokens[0] + '*')
    sql = """
        SELECT s.id, s.name_official, s.entity_type, s.street_type,
               s.city_id, s.district_id, s.province_id,
               c.name AS city, d.name AS district, p.name AS province,
               bm25(fts_address) AS rank
        FROM fts_address f
        JOIN streets_and_areas s ON s.id = f.entity_id
        LEFT JOIN cities c ON c.id = s.city_id
        LEFT JOIN districts d ON d.id = s.district_id
        LEFT JOIN provinces p ON p.id = s.province_id
        WHERE fts_address MATCH ?
        ORDER BY rank, length(s.name_norm)
        LIMIT ?
    """
    for v in variants:
        try:
            rows = conn.execute(sql, (v, limit)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if rows:
            return v, rows
    return variants[-1] if variants else q, []


def report(conn, db_path, log, t_start, test_query):
    def cnt(sql, *a):
        return conn.execute(sql, a).fetchone()[0]

    n_prov = cnt('SELECT COUNT(*) FROM provinces')
    n_city = cnt('SELECT COUNT(*) FROM cities')
    n_dist = cnt('SELECT COUNT(*) FROM districts')
    n_street = cnt("SELECT COUNT(*) FROM streets_and_areas WHERE entity_type='street'")
    n_area = cnt("SELECT COUNT(*) FROM streets_and_areas WHERE entity_type='living_area'")
    has_houses = bool(conn.execute(
        "SELECT name FROM sqlite_master WHERE name='houses'").fetchone())
    n_house = cnt('SELECT COUNT(*) FROM houses') if has_houses else 0
    n_house_linked = (cnt('SELECT COUNT(*) FROM houses WHERE street_id IS NOT NULL')
                      if has_houses else 0)
    n_area_linked = (cnt('SELECT COUNT(*) FROM houses WHERE place_id IS NOT NULL')
                     if has_houses else 0)

    conn.execute("INSERT INTO fts_address(fts_address) VALUES('optimize')")
    conn.commit()
    conn.execute('PRAGMA journal_mode=DELETE')
    conn.execute('PRAGMA synchronous=FULL')
    conn.commit()
    conn.execute('VACUUM')
    conn.commit()

    size_mb = os.path.getsize(db_path) / 1024 / 1024
    elapsed = time.time() - t_start

    print('\n' + '=' * 78)
    print('ИТОГОВЫЙ ОТЧЁТ')
    print('=' * 78)
    print(f'  Файл БД ...................... {db_path}')
    print(f'  Размер файла ................. {size_mb:.1f} MB')
    print(f'  Области (provinces) .......... {n_prov}')
    print(f'  Города (cities) .............. {n_city}')
    print(f'  Районы (districts) ........... {n_dist}')
    print(f'  Улицы (street) ............... {n_street}')
    print(f'  Микрорайоны (living_area) .... {n_area}')
    print(f'  Улиц+микрорайонов всего ...... {n_street + n_area}')
    if has_houses:
        print(f'  Дома (houses) ................ {n_house} '
              f'(к улице: {n_house_linked}, к жилмассиву: {n_area_linked})')
    s_city = cnt('SELECT COUNT(*) FROM streets_and_areas WHERE city_id IS NOT NULL')
    h_city = (cnt('SELECT COUNT(*) FROM houses WHERE city_id IS NOT NULL')
              if has_houses else 0)
    print(f'  Улиц/массивов с city_id ...... {s_city} '
          f'({100.0 * s_city / max(1, n_street + n_area):.1f}%)')
    if has_houses:
        print(f'  Домов с city_id .............. {h_city} '
              f'({100.0 * h_city / max(1, n_house):.1f}%)')
    print(f'  Пик RSS ...................... {peak_rss_mb():.0f} MB')
    print(f'  Время ........................ {elapsed:.0f} s')
    print('-' * 78)

    q, rows = fts_test(conn, test_query)
    print(f'ТЕСТОВЫЙ FTS-ЗАПРОС: "{test_query}"   (использован вариант: {q})')
    if not rows:
        print('  нет результатов')
    for i, r in enumerate(rows, 1):
        (eid, nm, etype, stype, city_id, district_id, province_id,
         city, dist, prov, _rank) = r
        print(f'  {i}. {nm}  [{etype}/{stype or "-"}]  entity_id={eid}')
        print(f'     district_id={district_id} ({dist or "-"}) | '
              f'city_id={city_id} ({city or "-"}) | '
              f'province_id={province_id} ({prov or "-"})')
    print('=' * 78)

    conn.execute('INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)',
                 ('report', json.dumps({
                     'provinces': n_prov, 'cities': n_city, 'districts': n_dist,
                     'streets': n_street, 'living_areas': n_area,
                     'houses': n_house, 'houses_linked': n_house_linked,
                     'living_areas_linked': n_area_linked,
                     'streets_with_city': s_city, 'houses_with_city': h_city,
                     'size_mb': round(size_mb, 1),
                     'elapsed_s': round(elapsed, 1),
                     'peak_rss_mb': round(peak_rss_mb(), 1),
                 }, ensure_ascii=False)))
    conn.commit()
    return rows


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def find_pbf(explicit: str | None) -> str:
    if explicit:
        if not os.path.exists(explicit):
            raise SystemExit(f'PBF не найден: {explicit}')
        return explicit
    for cand in ('kazakhstan-latest.osm.pbf',):
        if os.path.exists(cand):
            return cand
    found = sorted(glob.glob('*.osm.pbf'))
    if found:
        return found[0]
    raise SystemExit('Не найден ни один *.osm.pbf (укажите --pbf)')


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='ETL: OSM PBF (KZ) + КАТО -> SQLite address_base.db')
    ap.add_argument('--pbf', default=None, help='дамп OSM (kazakhstan-latest.osm.pbf)')
    ap.add_argument('--db', default='address_base.db', help='выходная SQLite')
    ap.add_argument('--kato', default=None, help='файл КАТО (JSON или CSV)')
    ap.add_argument('--level8', choices=['auto', 'all', 'none'], default='auto',
                    help='admin_level=8 как города: auto=только городские/поселковые, '
                         'all=все (вкл. ауылдық округі), none=не использовать')
    ap.add_argument('--no-houses', action='store_true', help='не выгружать дома')
    ap.add_argument('--test-query', default='аксай 3а', help='FTS-запрос для отчёта')
    args = ap.parse_args(argv)

    t_start = time.time()
    log = Timer().mark
    pbf = find_pbf(args.pbf)

    print('=' * 78)
    print(f'ETL: {pbf}  ->  {args.db}')
    print(f'     kato={args.kato or "-"}  level8={args.level8}  '
          f'houses={not args.no_houses}')
    print('=' * 78)

    # --- pass 1: границы ---
    kz_border, provinces, districts, level8, place_areas = pass_admin(pbf, log)

    # --- pass 2: place-ноды ---
    place_nodes = pass_place_nodes(pbf, log)

    # --- сборка иерархии ---
    prov_items, prov_idx, cities, city_idx, dist_items, dist_idx = build_hierarchy(
        kz_border, provinces, districts, level8, place_areas, place_nodes,
        args.level8, log)

    # --- запись админ-уровня ---
    conn = open_db(args.db)
    P, C = write_admin(conn, prov_items, cities, dist_items)
    conn.execute('INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)',
                 ('source_pbf', pbf))
    conn.commit()
    log('sqlite: provinces/cities/districts записаны')

    # --- pass 3: улицы / микрорайоны / дома ---
    n_sa, n_house = pass_streets(conn, pbf, kz_border, prov_idx, city_idx, dist_idx,
                                 (P, C), not args.no_houses, log)

    if not args.no_houses:
        resolve_house_streets(conn, log)
        fill_orphan_city_ids(conn, log)
        n_promo = promote_address_entities(conn, log)
        # досозданные из addr:street сущности могли остаться без city_id — повторный проход
        fill_orphan_city_ids(conn, log)
    else:
        n_promo = 0

    # --- КАТО ---
    if args.kato:
        try:
            rows = load_kato(args.kato)
            write_kato(conn, rows)
            n_map = match_kato(conn, prov_items, cities, dist_items)
            log(f'КАТО: записей={len(rows)}, сопоставлено с базой={n_map}')
        except Exception as e:                       # noqa: BLE001
            log(f'КАТО: WARN не удалось загрузить ({e})')
    else:
        log('КАТО: не передан (--kato), пропуск')

    create_indexes(conn)
    report(conn, args.db, log, t_start, args.test_query)
    conn.close()


if __name__ == '__main__':
    main()
