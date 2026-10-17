import { FIELDS, FIELD_KEYS, OUTPUT_KEYS } from './fields';

export function parseInputFile(text) {
  let data;
  try {
    data = JSON.parse(text.replace(/^﻿/, ''));
  } catch {
    throw new Error('Файл не похож на JSON. Ожидается массив вида [{ "id": "...", "raw_address": "..." }].');
  }
  if (!Array.isArray(data)) {
    data = Array.isArray(data?.records) ? data.records : null;
  }
  if (!data) throw new Error('В файле должен быть массив записей.');
  if (data.length === 0) throw new Error('Файл пустой — в массиве нет ни одной записи.');

  return data.map((item, index) => {
    if (!item || typeof item.raw_address !== 'string') {
      throw new Error(`Запись №${index + 1}: нет строкового поля raw_address.`);
    }
    return { id: String(item.id ?? `rec_${index + 1}`), raw_address: item.raw_address };
  });
}

export function linesToRecords(text) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((raw_address, index) => ({ id: `manual_${String(index + 1).padStart(4, '0')}`, raw_address }));
}

export function emptyResult(input) {
  const result = { id: input.id, raw_address: input.raw_address, confidence: 0 };
  for (const key of FIELD_KEYS) result[key] = null;
  return result;
}

export function normalizeResult(input, parsed) {
  const result = emptyResult(input);
  if (!parsed) return result;
  for (const key of FIELD_KEYS) {
    const value = parsed[key];
    result[key] = value === undefined || value === null || String(value).trim() === '' ? null : String(value);
  }
  const confidence = Number(parsed.confidence);
  result.confidence = Number.isFinite(confidence) ? Math.min(1, Math.max(0, confidence)) : 0;
  if (parsed.sources && typeof parsed.sources === 'object') result.sources = parsed.sources;
  if (Number.isFinite(parsed.lat) && Number.isFinite(parsed.lng)) {
    result.lat = parsed.lat;
    result.lng = parsed.lng;
  }
  return result;
}

export function toOutput(record) {
  const out = {};
  for (const key of OUTPUT_KEYS) out[key] = record[key] ?? null;
  return out;
}

export function downloadJson(fileName, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

const lower = (s) => s.toLocaleLowerCase('ru');

const STREET_TYPE_SHORT = {
  улица: 'ул.',
  проспект: 'пр.',
  микрорайон: 'мкр',
  переулок: 'пер.',
  квартал: 'кв-л',
  бульвар: 'б-р',
  шоссе: 'ш.',
};

export function streetLabel(record) {
  if (!record.street_name) return null;
  const type = record.street_type ? STREET_TYPE_SHORT[record.street_type] ?? record.street_type : null;
  return [type, record.street_name].filter(Boolean).join(' ');
}

export function parsedSummary(record) {
  return [
    record.city || record.province,
    streetLabel(record),
    record.house && `д. ${record.house}`,
    record.apartment && `кв. ${record.apartment}`,
    record.entrance && `под. ${record.entrance}`,
    record.floor && `эт. ${record.floor}`,
  ]
    .filter(Boolean)
    .join(', ');
}

const tokens = (s) => lower(s).split(/[^\p{L}\p{N}]+/u).filter((t) => t.length > 1 || /\d/.test(t));

export function mapQueries(record) {
  const locality = record.city || record.province;
  if (!locality) return [];
  const name = record.street_name;
  const type = record.street_type && record.street_type !== 'улица' ? record.street_type : '';
  const house = record.house;
  const localityMatch = tokens(locality);
  const streetMatch = name ? [...localityMatch, ...tokens(name)] : localityMatch;
  const candidates = [];
  const add = (parts, exact, match, extra) => {
    const q = [locality, parts.filter(Boolean).join(' ')].filter(Boolean).join(', ');
    if (!candidates.some((c) => c.q === q)) candidates.push({ q, exact, match, ...extra });
  };
  if (name) {
    add([type, name, house], Boolean(house), streetMatch);
    if (/\s/.test(name)) add([type, name.replace(/\s+/g, '-'), house], Boolean(house), streetMatch);
    add([name, house], Boolean(house), streetMatch);
    add([type, name], false, streetMatch);
    if (/\s/.test(name)) add([name.replace(/\s+/g, '-')], false, streetMatch);
  }
  const area = record.living_area;
  if (area) {
    const areaMatch = [...localityMatch, ...tokens(area)];
    const dashed = area.replace(/\s+(?=\d)/g, '-');
    const extra = !name && house ? { house } : undefined;
    if (extra) {
      if (dashed !== area) add([dashed, house], true, areaMatch, extra);
      add([area, house], true, areaMatch, extra);
    }
    if (dashed !== area) add([dashed], false, areaMatch, extra);
    add([area], false, areaMatch, extra);
  }
  if (record.district) add([`${record.district} район`], false, localityMatch);
  add([], false, localityMatch);
  return candidates;
}

export function fieldSource(record, key) {
  const value = record[key];
  if (value == null) return null;
  const declared = record.sources?.[key];
  if (declared) return declared === 'text' ? 'text' : 'reference';
  if (!FIELDS.find((f) => f.key === key)?.reference) return 'text';
  const raw = lower(record.raw_address);
  const stem = lower(value).slice(0, Math.max(4, Math.min(6, value.length)));
  return raw.includes(stem) ? 'text' : 'reference';
}

export function highlightRanges(record) {
  const raw = lower(record.raw_address);
  const taken = [];
  const candidates = FIELDS.map((f) => ({ key: f.key, value: record[f.key] }))
    .filter((c) => c.value && c.key !== 'street_type')
    .sort((a, b) => b.value.length - a.value.length);

  for (const { key, value } of candidates) {
    const needle = lower(value);
    let from = 0;
    while (from <= raw.length) {
      const start = raw.indexOf(needle, from);
      if (start === -1) break;
      const end = start + needle.length;
      const overlaps = taken.some((r) => start < r.end && end > r.start);
      const glued =
        (/^\p{N}/u.test(needle) && /[\p{N}/]/u.test(raw[start - 1] || '')) ||
        (/\p{N}$/u.test(needle) && /[\p{N}/]/u.test(raw[end] || ''));
      if (!overlaps && !glued) {
        taken.push({ start, end, key });
        break;
      }
      from = start + 1;
    }
  }
  return taken.sort((a, b) => a.start - b.start);
}
