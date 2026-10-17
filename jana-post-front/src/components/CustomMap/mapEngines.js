import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { TILE_PROVIDERS, attachTileFallback } from './tileProviders';

function pinSvg(color) {
  return `<svg width="30" height="42" viewBox="0 0 30 42" xmlns="http://www.w3.org/2000/svg">
    <path d="M15 0C6.7 0 0 6.7 0 15c0 10.5 15 27 15 27s15-16.5 15-27C30 6.7 23.3 0 15 0z"
          fill="${color}" stroke="#ffffff" stroke-width="2"/>
    <circle cx="15" cy="15" r="5" fill="#ffffff"/>
  </svg>`;
}

const PIN_SIZE = [30, 42];

export function createLeafletEngine({ container, center, zoom, marker, pinColor, tileIndex, fallbackIndex, onClick, onTileStatus }) {
  const map = L.map(container, {
    center,
    zoom,
    zoomControl: false,
    attributionControl: true,
  });
  L.control.zoom({ position: 'topright' }).addTo(map);
  map.attributionControl.setPrefix(false);
  map.attributionControl.getContainer().tabIndex = 0;

  const icon = L.divIcon({
    className: 'cm-pin',
    html: pinSvg(pinColor),
    iconSize: PIN_SIZE,
    iconAnchor: [PIN_SIZE[0] / 2, PIN_SIZE[1]],
  });

  let markerLayer = null;
  const setMarker = (lat, lng) => {
    if (markerLayer) markerLayer.setLatLng([lat, lng]);
    else markerLayer = L.marker([lat, lng], { icon }).addTo(map);
  };
  const clearMarker = () => {
    markerLayer?.remove();
    markerLayer = null;
  };
  if (marker) setMarker(marker.lat, marker.lng);

  const tiles = attachTileFallback(L, map, TILE_PROVIDERS, onTileStatus, tileIndex, fallbackIndex);

  map.on('click', (e) => onClick(e.latlng.lat, e.latlng.lng));

  const resizeTimer = setTimeout(() => map.invalidateSize(), 50);

  return {
    kind: 'leaflet',
    flyTo: (lat, lng, targetZoom = 16) => map.flyTo([lat, lng], targetZoom, { duration: 0.6 }),
    setMarker,
    clearMarker,
    getView: () => {
      const c = map.getCenter();
      return { center: [c.lat, c.lng], zoom: map.getZoom() };
    },
    setTileProvider: (index) => tiles.setProvider(index),
    destroy: () => {
      clearTimeout(resizeTimer);
      tiles.detach();
      map.remove();
    },
  };
}

export function createYandexEngine({ ymaps, container, center, zoom, marker, pinColor, onClick }) {
  const map = new ymaps.Map(
    container,
    { center, zoom: Math.round(zoom), controls: [] },
    { suppressMapOpenBlock: true, yandexMapDisablePoiInteractivity: true }
  );
  map.controls.add('zoomControl', { size: 'small', position: { top: 16, right: 16 } });

  const iconImageHref = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(pinSvg(pinColor))}`;
  let placemark = null;
  const setMarker = (lat, lng) => {
    if (placemark) {
      placemark.geometry.setCoordinates([lat, lng]);
      return;
    }
    placemark = new ymaps.Placemark(
      [lat, lng],
      {},
      {
        iconLayout: 'default#image',
        iconImageHref,
        iconImageSize: PIN_SIZE,
        iconImageOffset: [-PIN_SIZE[0] / 2, -PIN_SIZE[1]],
      }
    );
    map.geoObjects.add(placemark);
  };
  const clearMarker = () => {
    if (placemark) map.geoObjects.remove(placemark);
    placemark = null;
  };
  if (marker) setMarker(marker.lat, marker.lng);

  map.events.add('click', (e) => {
    const [lat, lng] = e.get('coords');
    onClick(lat, lng);
  });

  return {
    kind: 'yandex',
    flyTo: (lat, lng, targetZoom = 16) => map.setCenter([lat, lng], targetZoom, { duration: 300 }),
    setMarker,
    clearMarker,
    getView: () => ({ center: map.getCenter(), zoom: map.getZoom() }),
    setTileProvider: () => {},
    destroy: () => map.destroy(),
  };
}
