import { OFFLINE_PROVIDERS, createOfflineLayer } from './offlineTiles';

const CARTO_API_KEY = import.meta.env.VITE_CARTO_API_KEY;

const CARTO_PROVIDERS = CARTO_API_KEY
  ? [
      {
        id: 'carto-voyager',
        label: 'CARTO Voyager',
        url: `https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?key=${CARTO_API_KEY}`,
        subdomains: 'abcd',
        attribution: '© OpenStreetMap contributors © CARTO',
        maxZoom: 20,
      },
      {
        id: 'carto-positron',
        label: 'CARTO Positron',
        url: `https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png?key=${CARTO_API_KEY}`,
        subdomains: 'abcd',
        attribution: '© OpenStreetMap contributors © CARTO',
        maxZoom: 20,
      },
      {
        id: 'carto-dark',
        label: 'CARTO Dark Matter',
        url: `https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png?key=${CARTO_API_KEY}`,
        subdomains: 'abcd',
        attribution: '© OpenStreetMap contributors © CARTO',
        maxZoom: 20,
      },
    ]
  : [];

export const TILE_PROVIDERS = [
  ...CARTO_PROVIDERS,
  {
    id: 'osm',
    label: 'OpenStreetMap',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap contributors',
    maxZoom: 19,
  },
  {
    id: 'esri',
    label: 'Esri World Street Map',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Esri, HERE, Garmin, FAO, NOAA, USGS',
    maxZoom: 19,
  },
  {
    id: 'wikimedia',
    label: 'Wikimedia Maps',
    url: 'https://maps.wikimedia.org/osm-intl/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap contributors, Wikimedia',
    maxZoom: 19,
  },
  {
    id: 'opentopomap',
    label: 'OpenTopoMap',
    url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    subdomains: 'abc',
    attribution: '© OpenStreetMap contributors, SRTM | © OpenTopoMap',
    maxZoom: 17,
  },
  ...OFFLINE_PROVIDERS,
];

export const YANDEX_PROVIDER = { id: 'yandex', label: 'Яндекс Карты', engine: 'yandex' };

export const MAP_STYLE_OPTIONS = [
  ...TILE_PROVIDERS.map((provider) => ({ id: provider.id, label: provider.label, engine: 'leaflet' })),
  YANDEX_PROVIDER,
];

export const TILE_FALLBACK_TIMEOUT_MS = 6000;

function createTileLayer(L, provider) {
  if (provider.offline) return createOfflineLayer(L, provider);
  return L.tileLayer(provider.url, {
    subdomains: provider.subdomains || 'abc',
    attribution: provider.attribution,
    maxZoom: provider.maxZoom || 19,
    crossOrigin: true,
  });
}

export function attachTileFallback(L, map, providers, onStatusChange, startIndex = 0, fallbackIndex = -1) {
  let index = startIndex;
  let currentLayer = null;
  let timer = null;
  let gotTile = false;
  let detached = false;

  const startLayer = (i, { manual = false } = {}) => {
    if (timer) clearTimeout(timer);
    if (currentLayer) {
      currentLayer.off('tileload tileerror');
      map.removeLayer(currentLayer);
    }

    index = i;
    gotTile = false;
    const provider = providers[index];
    currentLayer = createTileLayer(L, provider);
    currentLayer.on('tileload', () => {
      gotTile = true;
      if (timer) clearTimeout(timer);
    });
    currentLayer.on('tileerror', (e) => {
      console.error(`[CustomMap] Ошибка тайла у «${provider.label}»:`, e?.error || e);
    });
    currentLayer.addTo(map);

    timer = setTimeout(() => {
      if (!gotTile && !detached) advanceAuto();
    }, TILE_FALLBACK_TIMEOUT_MS);

    onStatusChange?.({ id: provider.id, label: provider.label, index, manual, failed: false });
  };

  const advanceAuto = () => {
    const failed = providers[index];
    if (fallbackIndex >= 0 && !failed.offline) {
      console.warn(`[CustomMap] Тайлы «${failed.label}» не пришли за ${TILE_FALLBACK_TIMEOUT_MS}мс. Переключаюсь на офлайн-карту.`);
      startIndex = fallbackIndex;
      startLayer(fallbackIndex);
      return;
    }
    const nextIndex = (index + 1) % providers.length;

    if (nextIndex === startIndex) {
      console.error(
        `[CustomMap] Ни один из ${providers.length} тайл-провайдеров не ответил. ` +
          `Похоже, блокируется не конкретный сервис, а любые внешние картографические CDN.`
      );
      onStatusChange?.({ id: failed.id, label: failed.label, index, failed: true });
      return;
    }

    console.warn(
      `[CustomMap] Тайлы «${failed.label}» не пришли за ${TILE_FALLBACK_TIMEOUT_MS}мс. ` +
        `Пробую «${providers[nextIndex].label}».`
    );
    startLayer(nextIndex);
  };

  startLayer(index);

  return {
    getIndex: () => index,
    setProvider: (i) => {
      if (i < 0 || i >= providers.length || i === index) return;
      startIndex = i;
      startLayer(i, { manual: true });
    },
    detach: () => {
      detached = true;
      if (timer) clearTimeout(timer);
      currentLayer?.off('tileload tileerror');
    },
  };
}
