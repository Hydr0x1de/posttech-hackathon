export function createNominatimProvider({ language = 'ru', countryCodes = '' } = {}) {
  return {
    name: 'nominatim',

    async search(query, { signal } = {}) {
      if (!query || query.trim().length < 3) return [];

      const url = new URL('https://nominatim.openstreetmap.org/search');
      url.searchParams.set('format', 'jsonv2');
      url.searchParams.set('addressdetails', '1');
      url.searchParams.set('limit', '6');
      url.searchParams.set('q', query);
      url.searchParams.set('accept-language', language);
      if (countryCodes) url.searchParams.set('countrycodes', countryCodes);

      const res = await fetch(url.toString(), { signal });
      if (!res.ok) throw new Error(`Nominatim error: ${res.status}`);
      const data = await res.json();
      return data.map(normalizeNominatimItem);
    },

    async reverse(lng, lat, { signal } = {}) {
      const url = new URL('https://nominatim.openstreetmap.org/reverse');
      url.searchParams.set('format', 'jsonv2');
      url.searchParams.set('addressdetails', '1');
      url.searchParams.set('lat', String(lat));
      url.searchParams.set('lon', String(lng));
      url.searchParams.set('accept-language', language);

      const res = await fetch(url.toString(), { signal });
      if (!res.ok) throw new Error(`Nominatim error: ${res.status}`);
      const data = await res.json();
      if (!data || data.error) return null;
      return normalizeNominatimItem(data);
    },

    // Дом по номеру внутри границ найденного места: в OSM он бывает подписан
    // другим микрорайоном («59, Аксай-3» стоит внутри «Аксай 3А»)
    async findHouse(house, place, { signal } = {}) {
      const box = place?.raw?.boundingbox?.map(Number);
      if (!house || !box || box.some((n) => !Number.isFinite(n))) return null;
      const [s, n, w, e] = box;
      const padLat = Math.max((n - s) * 0.2, 0.002);
      const padLng = Math.max((e - w) * 0.2, 0.002);
      const bbox = [s - padLat, w - padLng, n + padLat, e + padLng].map((v) => v.toFixed(6)).join(',');
      const number = house.trim().replace(/\s+/g, '').replace(/[.*+?^${}()|[\]\\"]/g, '\\$&');
      const query = `[out:json][timeout:15];nwr["addr:housenumber"~"^${number}$",i](${bbox});out tags center 10;`;

      const data = await overpass(query, signal);

      const lat0 = parseFloat(place.raw.lat);
      const lng0 = parseFloat(place.raw.lon);
      const found = (data.elements || [])
        .map((el) => ({ el, lat: el.lat ?? el.center?.lat, lng: el.lon ?? el.center?.lon }))
        .filter((h) => Number.isFinite(h.lat) && Number.isFinite(h.lng))
        .sort((a, b) => Math.hypot(a.lat - lat0, a.lng - lng0) - Math.hypot(b.lat - lat0, b.lng - lng0))[0];
      if (!found) return null;

      const tags = found.el.tags || {};
      const street = tags['addr:street'] || tags['addr:place'] || '';
      return {
        id: `osm-${found.el.type}-${found.el.id}`,
        label: [tags['addr:housenumber'], street, place.normalized?.city].filter(Boolean).join(', '),
        name: tags.name || '',
        lng: found.lng,
        lat: found.lat,
        normalized: {
          ...place.normalized,
          street,
          house: tags['addr:housenumber'],
          full: [street, tags['addr:housenumber']].filter(Boolean).join(', '),
        },
        raw: found.el,
      };
    },
  };
}

// Публичные серверы Overpass часто отвечают 429/502 — пробуем зеркала по очереди
const OVERPASS_ENDPOINTS = [
  'https://overpass-api.de/api/interpreter',
  'https://overpass.kumi.systems/api/interpreter',
  'https://overpass.private.coffee/api/interpreter',
];

async function overpass(query, signal) {
  let lastError;
  for (const endpoint of OVERPASS_ENDPOINTS) {
    try {
      const res = await fetch(endpoint, { method: 'POST', body: new URLSearchParams({ data: query }), signal });
      if (res.ok) return await res.json();
      lastError = new Error(`Overpass error: ${res.status}`);
    } catch (err) {
      if (err.name === 'AbortError') throw err;
      lastError = err;
    }
  }
  throw lastError;
}

function normalizeNominatimItem(item) {
  const a = item.address || {};
  const street = a.road || a.pedestrian || a.street || '';
  const house = a.house_number || '';
  const city = a.city || a.town || a.village || a.municipality || '';
  const region = a.state || a.region || '';
  const streetLine = [street, house].filter(Boolean).join(', ');

  return {
    id: String(item.place_id),
    label: item.display_name,
    name: item.name || '',
    lng: parseFloat(item.lon),
    lat: parseFloat(item.lat),
    normalized: {
      country: a.country || '',
      region,
      city,
      street,
      house,
      postcode: a.postcode || '',
      full: streetLine || item.name || item.display_name,
    },
    raw: item,
  };
}

export function formatAddress(place) {
  if (!place?.normalized) return place?.label || '';
  const { city, region, street, house } = place.normalized;
  const streetLine = [street, house].filter(Boolean).join(', ');
  const locality = city || region;

  if (streetLine) return [locality, streetLine].filter(Boolean).join(', ');
  if (place.name) return [locality, place.name === locality ? '' : place.name].filter(Boolean).join(', ');
  return place.label || '';
}

export function formatPlaceMeta(normalized) {
  if (!normalized) return '';
  const main = [normalized.city, normalized.region, normalized.country].filter(Boolean).join(', ');
  return normalized.postcode ? `${main} · ${normalized.postcode}` : main;
}

export function createMapTilerProvider({ apiKey, language = 'ru' }) {
  if (!apiKey) {
    throw new Error(
      'createMapTilerProvider: нужен apiKey — бесплатный ключ с cloud.maptiler.com'
    );
  }

  return {
    name: 'maptiler',

    async search(query, { signal } = {}) {
      if (!query || query.trim().length < 3) return [];

      const url = new URL(
        `https://api.maptiler.com/geocoding/${encodeURIComponent(query)}.json`
      );
      url.searchParams.set('key', apiKey);
      url.searchParams.set('language', language);
      url.searchParams.set('limit', '6');

      const res = await fetch(url.toString(), { signal });
      if (!res.ok) throw new Error(`MapTiler error: ${res.status}`);
      const data = await res.json();
      return (data.features || []).map(normalizeMapTilerFeature);
    },

    async reverse(lng, lat, { signal } = {}) {
      const url = new URL(`https://api.maptiler.com/geocoding/${lng},${lat}.json`);
      url.searchParams.set('key', apiKey);
      url.searchParams.set('language', language);

      const res = await fetch(url.toString(), { signal });
      if (!res.ok) throw new Error(`MapTiler error: ${res.status}`);
      const data = await res.json();
      const feature = (data.features || [])[0];
      return feature ? normalizeMapTilerFeature(feature) : null;
    },
  };
}

function normalizeMapTilerFeature(feature) {
  const [lng, lat] = feature.center || [];
  const byKind = (kind) =>
    (feature.context || []).find((c) => String(c.id).startsWith(`${kind}.`))?.text || '';

  const houseAndStreet = feature.place_type?.includes('address') ? feature.text : '';

  return {
    id: feature.id,
    label: feature.place_name,
    lng,
    lat,
    normalized: {
      country: byKind('country'),
      region: byKind('region'),
      city: byKind('place') || byKind('municipality'),
      street: houseAndStreet,
      house: feature.address || '',
      postcode: byKind('postal_code'),
      full: feature.place_name,
    },
    raw: feature,
  };
}

