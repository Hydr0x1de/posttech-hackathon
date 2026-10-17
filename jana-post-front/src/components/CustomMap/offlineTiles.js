import { namedFlavor } from '@protomaps/basemaps';
import {
  CenteredTextSymbolizer,
  CircleSymbolizer,
  GroupSymbolizer,
  LineLabelSymbolizer,
  OffsetTextSymbolizer,
  labelRules,
  leafletLayer,
  paintRules,
} from 'protomaps-leaflet';

const BASE_URL = '/tiles/kz-base.pmtiles';
const CITIES_URL = '/tiles/kz-cities.pmtiles';
const ATTRIBUTION = '© OpenStreetMap contributors · Protomaps';
const FONT = '"Inter Variable", sans-serif';
const NAME = ['name:ru', 'name'];

export const OFFLINE_PROVIDERS = [
  { id: 'offline-light', label: 'Офлайн-карта', flavor: 'light', offline: true, attribution: ATTRIBUTION, maxZoom: 19 },
  { id: 'offline-dark', label: 'Офлайн-карта (тёмная)', flavor: 'dark', offline: true, attribution: ATTRIBUTION, maxZoom: 19 },
];

const POI_GROUPS = [
  { color: 'tangerine', kinds: ['restaurant', 'cafe', 'fast_food', 'bar', 'pub', 'coffee', 'food_court', 'ice_cream', 'bakery'] },
  { color: 'red', kinds: ['hospital', 'clinic', 'pharmacy', 'dentist', 'doctors', 'veterinary'] },
  { color: 'green', kinds: ['park', 'garden', 'playground', 'sports', 'sports_centre', 'stadium', 'fitness_centre', 'pitch', 'zoo'] },
  { color: 'lapis', kinds: ['bus_stop', 'station', 'tram_stop', 'subway_entrance', 'bus_station', 'parking', 'fuel', 'aerodrome'] },
  { color: 'slategray', kinds: ['school', 'kindergarten', 'university', 'college', 'library', 'museum', 'theatre', 'cinema', 'arts_centre'] },
  { color: 'blue', kinds: ['bank', 'atm', 'bureau_de_change', 'post_office', 'townhall', 'public_building', 'police', 'courthouse'] },
  { color: 'pink', kinds: ['hotel', 'hostel', 'guest_house', 'place_of_worship', 'mosque', 'church'] },
];

const HIDDEN_POI = new Set(['crossing', 'traffic_signals', 'payment_terminal', 'administrative', 'yes', 'outpost']);

function poiColor(flavor, kind) {
  const group = POI_GROUPS.find((g) => g.kinds.includes(kind));
  return group ? flavor.pois[group.color] : flavor.pois.turquoise;
}

function detailLabelRules(flavor, dark) {
  const halo = flavor.roads_label_minor_halo;
  return [
    {
      dataLayer: 'pois',
      minzoom: 12,
      maxzoom: 15,
      symbolizer: new CenteredTextSymbolizer({
        labelProps: NAME,
        fill: dark ? '#9aa39d' : '#7a817c',
        stroke: halo,
        width: 2,
        font: `600 11px ${FONT}`,
        textTransform: 'uppercase',
        letterSpacing: 1,
      }),
      filter: (z, f) => f.props.kind === 'administrative',
    },
    {
      dataLayer: 'roads',
      minzoom: 14,
      maxzoom: 15,
      symbolizer: new LineLabelSymbolizer({
        labelProps: NAME,
        fill: flavor.roads_label_minor,
        stroke: halo,
        width: 2,
        font: `400 11px ${FONT}`,
      }),
      filter: (z, f) => ['minor_road', 'other'].includes(f.props.kind),
    },
    {
      dataLayer: 'pois',
      minzoom: 14,
      symbolizer: new GroupSymbolizer([
        new CircleSymbolizer({
          radius: 3.5,
          fill: (z, f) => poiColor(flavor, f?.props.kind),
          stroke: halo,
          width: 1.5,
        }),
        new OffsetTextSymbolizer({
          labelProps: NAME,
          fill: (z, f) => poiColor(flavor, f?.props.kind),
          stroke: halo,
          width: 2,
          font: `500 11px ${FONT}`,
          offsetX: 7,
          offsetY: 4,
          maxLineChars: 18,
        }),
      ]),
      sort: (a, b) => (a.min_zoom ?? 99) - (b.min_zoom ?? 99),
      filter: (z, f) => {
        const kind = f.props.kind;
        if (HIDDEN_POI.has(kind)) return false;
        if (!f.props['name:ru'] && !f.props.name) return false;
        return (f.props.min_zoom ?? 15) <= z + 1;
      },
    },
    {
      dataLayer: 'buildings',
      minzoom: 16,
      symbolizer: new CenteredTextSymbolizer({
        labelProps: ['addr_housenumber'],
        fill: dark ? '#8f9792' : flavor.address_label,
        stroke: flavor.address_label_halo,
        width: 2,
        font: `500 10px ${FONT}`,
      }),
      filter: (z, f) => Boolean(f.props.addr_housenumber),
    },
  ];
}

function detailedLayer(provider, options) {
  const flavor = namedFlavor(provider.flavor);
  const dark = provider.flavor === 'dark';
  return leafletLayer({
    ...options,
    lang: 'ru',
    maxZoom: provider.maxZoom,
    paintRules: paintRules(flavor),
    labelRules: [...labelRules(flavor, 'ru'), ...detailLabelRules(flavor, dark)],
    backgroundColor: options.transparent ? undefined : flavor.background,
  });
}

export function createOfflineLayer(L, provider) {
  const base = detailedLayer(provider, {
    url: BASE_URL,
    maxDataZoom: 10,
    attribution: provider.attribution,
  });

  const cities = detailedLayer(provider, {
    url: CITIES_URL,
    minZoom: 11,
    maxDataZoom: 15,
    zIndex: 2,
    attribution: '',
    transparent: true,
  });

  const addBase = base.onAdd.bind(base);
  const removeBase = base.onRemove.bind(base);
  base.onAdd = (map) => {
    addBase(map);
    cities.addTo(map);
    return base;
  };
  base.onRemove = (map) => {
    if (map.hasLayer(cities)) map.removeLayer(cities);
    removeBase(map);
    return base;
  };

  return base;
}
