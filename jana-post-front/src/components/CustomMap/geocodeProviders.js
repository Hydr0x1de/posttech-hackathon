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
  };
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

