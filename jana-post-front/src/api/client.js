import { emptyResult, normalizeResult } from '../address/records';
import { mockParseBatch } from './mockParser';

const API_BASE = (import.meta.env.VITE_API_BASE || '/api/v1').replace(/\/$/, '');
const USE_MOCK = import.meta.env.VITE_API_MOCK === 'true';
const CHUNK_SIZE = 500;

export const apiMode = USE_MOCK ? 'mock' : 'api';

async function postBatch(records, signal) {
  let res;
  try {
    res = await fetch(`${API_BASE}/parse/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(records),
      signal,
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('Сервис разбора недоступен. Проверьте, что бэкенд запущен.');
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`Сервис разбора ответил ошибкой ${res.status}${detail ? `: ${detail.slice(0, 200)}` : ''}`);
  }
  const data = await res.json();
  const list = Array.isArray(data) ? data : data?.results;
  if (!Array.isArray(list)) throw new Error('Сервис разбора вернул ответ в неожиданном формате.');
  return list;
}

export async function geocodeRecord(record, { signal } = {}) {
  const structured = record.city || record.street_name;
  const body = structured
    ? {
        id: record.id,
        city: record.city,
        street_name: record.street_name,
        house: record.house,
      }
    : { id: record.id, raw_address: record.raw_address };

  try {
    const res = await fetch(`${API_BASE}/geocode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });

    if (!res.ok) return null;
    const data = await res.json();
    return Number.isFinite(data.lat) && Number.isFinite(data.lon) ? data : null;
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    return null;
  }
}

export async function parseBatch(records, { onProgress, signal } = {}) {
  const byId = new Map();
  for (let i = 0; i < records.length; i += CHUNK_SIZE) {
    const chunk = records.slice(i, i + CHUNK_SIZE);
    const parsed = USE_MOCK ? await mockParseBatch(chunk) : await postBatch(chunk, signal);
    for (const item of parsed) if (item?.id != null) byId.set(String(item.id), item);
    onProgress?.(Math.min(records.length, i + CHUNK_SIZE) / records.length);
  }
  return records.map((input) => {
    const parsed = byId.get(input.id);
    return parsed ? normalizeResult(input, parsed) : emptyResult(input);
  });
}
