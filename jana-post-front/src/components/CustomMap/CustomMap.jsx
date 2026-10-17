import React, { useCallback, useEffect, useRef, useState } from 'react';

import { MAP_STYLE_OPTIONS, TILE_PROVIDERS, YANDEX_PROVIDER } from './tileProviders';
import { createLeafletEngine, createYandexEngine } from './mapEngines';
import { formatAddress, formatPlaceMeta } from './geocodeProviders';
import { loadYandexMaps } from './loadYandexMaps';
import { useGeocoder } from './useGeocoder';
import './CustomMap.css';

const DEFAULT_CENTER = [73.10937, 49.80469];
const STYLE_STORAGE_KEY = 'custom-map-style';
const DEFAULT_STYLE_ID = TILE_PROVIDERS[0].id;

function readSavedStyle(fallback = DEFAULT_STYLE_ID) {
  const known = (id) => MAP_STYLE_OPTIONS.some((option) => option.id === id);
  const safeFallback = known(fallback) ? fallback : DEFAULT_STYLE_ID;
  try {
    const saved = window.localStorage.getItem(STYLE_STORAGE_KEY);
    return known(saved) ? saved : safeFallback;
  } catch {
    return safeFallback;
  }
}

function saveStyle(id) {
  try {
    window.localStorage.setItem(STYLE_STORAGE_KEY, id);
  } catch {}
}

function tileIndexOf(styleId) {
  const index = TILE_PROVIDERS.findIndex((provider) => provider.id === styleId);
  return index === -1 ? 0 : index;
}

export default function CustomMap({
  geocoder,
  initialCenter,
  initialZoom = 12,
  height = '100%',
  defaultStyleId,
  offlineStyleId = 'offline-light',
  focusPoint,
  focusQueries,
  onAddressChange,
  searchable = true,
}) {
  const geo = useGeocoder(geocoder);

  const [styleId, setStyleId] = useState(() =>
    navigator.onLine ? readSavedStyle(defaultStyleId) : offlineStyleId
  );
  const [ymaps, setYmaps] = useState(null);
  const [yandexFailed, setYandexFailed] = useState(false);
  const [tileStatus, setTileStatus] = useState(null);
  const [styleMenuOpen, setStyleMenuOpen] = useState(false);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [selected, setSelected] = useState(null);
  const [resolving, setResolving] = useState(false);

  const rootRef = useRef(null);
  const containerRef = useRef(null);
  const engineRef = useRef(null);
  const center = initialCenter || DEFAULT_CENTER;
  const viewRef = useRef({ center: [center[1], center[0]], zoom: initialZoom });
  const pointRef = useRef(null);
  const reverseIdRef = useRef(0);
  const styleIdRef = useRef(styleId);
  const offlineStyleIdRef = useRef(offlineStyleId);
  const styleMenuRef = useRef(null);
  const onAddressChangeRef = useRef(onAddressChange);

  const engineKind = styleId === YANDEX_PROVIDER.id ? 'yandex' : 'leaflet';
  const yandexLoading = engineKind === 'yandex' && !ymaps;

  useEffect(() => {
    styleIdRef.current = styleId;
  }, [styleId]);

  useEffect(() => {
    onAddressChangeRef.current = onAddressChange;
  }, [onAddressChange]);

  const pickPoint = useCallback(
    async (lat, lng) => {
      engineRef.current?.setMarker(lat, lng);
      pointRef.current = { lat, lng };
      setSuggestOpen(false);
      setSelected({ lat, lng, label: '', normalized: null });
      setResolving(true);

      const requestId = ++reverseIdRef.current;
      try {
        const result = await geocoder.reverse(lng, lat);
        if (requestId !== reverseIdRef.current) return;
        if (result) {
          setSelected({ ...result, lat, lng });
          onAddressChangeRef.current?.(result.normalized);
        } else {
          setSelected({ lat, lng, label: 'Адрес не найден для этой точки', normalized: null, notFound: true });
          onAddressChangeRef.current?.(null);
        }
      } catch (err) {
        if (requestId !== reverseIdRef.current) return;
        console.error('[CustomMap] Ошибка реверс-геокодинга:', err);
        setSelected({ lat, lng, label: 'Не удалось определить адрес', normalized: null, notFound: true });
        onAddressChangeRef.current?.(null);
      } finally {
        if (requestId === reverseIdRef.current) setResolving(false);
      }
    },
    [geocoder]
  );

  const pickPointRef = useRef(pickPoint);
  useEffect(() => {
    pickPointRef.current = pickPoint;
  }, [pickPoint]);

  useEffect(() => {
    if (engineKind !== 'yandex' || ymaps) return undefined;
    let cancelled = false;

    loadYandexMaps()
      .then((instance) => {
        if (!cancelled) setYmaps(instance);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[CustomMap] Яндекс Карты не загрузились:', err);
        setYandexFailed(true);
        setStyleId(DEFAULT_STYLE_ID);
      });

    return () => {
      cancelled = true;
    };
  }, [engineKind, ymaps]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    if (engineKind === 'yandex' && !ymaps) return undefined;

    const pinColor =
      getComputedStyle(rootRef.current).getPropertyValue('--cm-accent').trim() || '#2447f5';

    const options = {
      container,
      center: viewRef.current.center,
      zoom: viewRef.current.zoom,
      marker: pointRef.current,
      pinColor,
      onClick: (lat, lng) => pickPointRef.current(lat, lng),
    };

    const engine =
      engineKind === 'yandex'
        ? createYandexEngine({ ...options, ymaps })
        : createLeafletEngine({
            ...options,
            tileIndex: tileIndexOf(styleIdRef.current),
            fallbackIndex: tileIndexOf(offlineStyleIdRef.current),
            onTileStatus: setTileStatus,
          });
    engineRef.current = engine;

    return () => {
      viewRef.current = engine.getView();
      engine.destroy();
      if (engineRef.current === engine) engineRef.current = null;
    };
  }, [engineKind, ymaps]);

  useEffect(() => {
    if (initialCenter || focusPoint || focusQueries !== undefined || !('geolocation' in navigator)) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        if (pointRef.current) return;
        engineRef.current?.flyTo(pos.coords.latitude, pos.coords.longitude, initialZoom);
      },
      () => {},
      { timeout: 5000 }
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!styleMenuOpen) return undefined;
    const handleOutside = (e) => {
      if (!styleMenuRef.current?.contains(e.target)) setStyleMenuOpen(false);
    };
    document.addEventListener('mousedown', handleOutside);
    return () => document.removeEventListener('mousedown', handleOutside);
  }, [styleMenuOpen]);

  const handleSelectSuggestion = (suggestion) => {
    reverseIdRef.current += 1;
    setResolving(false);
    engineRef.current?.flyTo(suggestion.lat, suggestion.lng, 16);
    engineRef.current?.setMarker(suggestion.lat, suggestion.lng);
    pointRef.current = { lat: suggestion.lat, lng: suggestion.lng };
    setSelected(suggestion);
    setSuggestOpen(false);
    onAddressChangeRef.current?.(suggestion.normalized);
    geo.clear();
  };

  const focusLat = focusPoint?.lat;
  const focusLng = focusPoint?.lng;
  const focusLabel = focusPoint?.label;

  useEffect(() => {
    if (!Number.isFinite(focusLat) || !Number.isFinite(focusLng)) return;
    reverseIdRef.current += 1;
    pointRef.current = { lat: focusLat, lng: focusLng };
    viewRef.current = { center: [focusLat, focusLng], zoom: 16 };
    engineRef.current?.flyTo(focusLat, focusLng, 16);
    engineRef.current?.setMarker(focusLat, focusLng);
    setResolving(false);
    setSelected({ lat: focusLat, lng: focusLng, label: focusLabel || '', normalized: null, notFound: true });
  }, [focusLat, focusLng, focusLabel]);

  const focusKey = focusQueries ? JSON.stringify(focusQueries) : null;

  useEffect(() => {
    if (Number.isFinite(focusLat) || focusKey === null) return undefined;
    const candidates = JSON.parse(focusKey);
    const requestId = ++reverseIdRef.current;

    if (candidates.length === 0) {
      setResolving(false);
      setSelected({ label: 'Мало данных для поиска на карте: нужен населённый пункт', normalized: null, notFound: true });
      pointRef.current = null;
      engineRef.current?.clearMarker();
      engineRef.current?.flyTo(48, 67, 4);
      return undefined;
    }

    const controller = new AbortController();
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const fits = (place, match = []) => {
      const words = new Set(
        `${place.label} ${place.name}`.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean)
      );
      return match.every((m) => words.has(m));
    };
    setResolving(true);
    setSelected({ label: '', normalized: null });

    (async () => {
      try {
        let approx = null;
        for (let i = 0; i < candidates.length; i += 1) {
          if (i > 0) await wait(1000);
          if (controller.signal.aborted) return;
          const results = await geocoder.search(candidates[i].q, { signal: controller.signal });
          if (requestId !== reverseIdRef.current) return;
          const matching = results.filter((place) => fits(place, candidates[i].match));
          let first = matching.find((place) => place.normalized?.house) || matching[0];
          if (candidates[i].exact && !first?.normalized?.house) {
            if (first) approx ??= first;
            if (candidates[i + 1]?.exact || !approx) continue;
            first = approx;
          }
          if (!first) continue;

          let exact = candidates[i].exact && Boolean(first.normalized?.house);
          if (!exact && candidates[i].house && geocoder.findHouse) {
            const house = await geocoder.findHouse(candidates[i].house, first, { signal: controller.signal }).catch((err) => {
              if (err.name === 'AbortError') throw err;
              return null;
            });
            if (requestId !== reverseIdRef.current) return;
            if (house) {
              first = house;
              exact = true;
            }
          }
          const zoom = exact ? 17 : i === candidates.length - 1 ? 12 : 15;
          pointRef.current = { lat: first.lat, lng: first.lng };
          viewRef.current = { center: [first.lat, first.lng], zoom };
          engineRef.current?.flyTo(first.lat, first.lng, zoom);
          engineRef.current?.setMarker(first.lat, first.lng);
          setSelected({ ...first, note: exact ? null : 'Точный дом не найден — показано приблизительно' });
          return;
        }
        pointRef.current = null;
        engineRef.current?.clearMarker();
        setSelected({ label: 'Геокодер не нашёл этот адрес', normalized: null, notFound: true });
      } catch (err) {
        if (err.name === 'AbortError' || requestId !== reverseIdRef.current) return;
        setSelected({ label: 'Геокодер недоступен — нет интернета?', normalized: null, notFound: true });
      } finally {
        if (requestId === reverseIdRef.current) setResolving(false);
      }
    })();

    return () => controller.abort();
  }, [focusKey, focusLat, geocoder]);

  const hasFocus = Number.isFinite(focusLat) || focusKey !== null;

  useEffect(() => {
    if (!hasFocus) return undefined;
    return () => {
      reverseIdRef.current += 1;
      pointRef.current = null;
      engineRef.current?.clearMarker();
      setResolving(false);
      setSelected(null);
    };
  }, [hasFocus]);

  const handleSearchKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (geo.suggestions[0]) handleSelectSuggestion(geo.suggestions[0]);
    } else if (e.key === 'Escape') {
      if (geo.query) geo.clear();
      else e.currentTarget.blur();
    }
  };

  const handlePickStyle = (option) => {
    setStyleMenuOpen(false);
    setYandexFailed(false);
    saveStyle(option.id);
    if (option.engine === 'leaflet' && engineRef.current?.kind === 'leaflet') {
      engineRef.current.setTileProvider(tileIndexOf(option.id));
    }
    setStyleId(option.id);
  };

  const activeStyleId = engineKind === 'yandex' ? YANDEX_PROVIDER.id : tileStatus?.id || styleId;
  const showSuggestions = suggestOpen && geo.active;

  return (
    <div ref={rootRef} className="cm-root" style={{ height }}>
      <div key={engineKind} ref={containerRef} className="cm-canvas" />

      {yandexLoading && (
        <div className="cm-loading">
          <span className="cm-spinner" />
        </div>
      )}

      {yandexFailed && (
        <div className="cm-banner cm-banner--error">
          Яндекс Карты не загрузились — показана обычная карта.
          <button type="button" onClick={() => setYandexFailed(false)} aria-label="Скрыть">
            ×
          </button>
        </div>
      )}

      {engineKind === 'leaflet' && tileStatus?.failed && (
        <div className="cm-banner cm-banner--error">
          {`Не загрузился ни один из ${TILE_PROVIDERS.length} провайдеров карты (последний: ${tileStatus.label}). Проверьте интернет-соединение или консоль браузера (F12).`}
        </div>
      )}

      <div className="cm-style-switcher" ref={styleMenuRef}>
        <button
          type="button"
          className={`cm-style-switcher__toggle${styleMenuOpen ? ' cm-style-switcher__toggle--open' : ''}`}
          onClick={() => setStyleMenuOpen((v) => !v)}
          aria-label="Сменить стиль карты"
          title="Стиль карты"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
            <path
              d="M12 2 2 7l10 5 10-5-10-5Z M2 17l10 5 10-5 M2 12l10 5 10-5"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          </svg>
        </button>
        {styleMenuOpen && (
          <div className="cm-style-switcher__menu">
            <div className="cm-style-switcher__title">Стиль карты</div>
            {MAP_STYLE_OPTIONS.map((option) => {
              const active = option.id === activeStyleId;
              return (
                <button
                  key={option.id}
                  type="button"
                  className={`cm-style-switcher__item${active ? ' cm-style-switcher__item--active' : ''}`}
                  onClick={() => handlePickStyle(option)}
                >
                  <span>{option.label}</span>
                  {active && (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                      <path
                        d="M20 6 9 17l-5-5"
                        stroke="currentColor"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {searchable && (
        <div className="cm-search">
          <div className="cm-search__field">
            <svg className="cm-search__icon" width="18" height="18" viewBox="0 0 24 24" fill="none">
              <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
              <path d="M20 20L16.5 16.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
            <input
              value={geo.query}
              onChange={(e) => {
                geo.setQuery(e.target.value);
                setSuggestOpen(true);
              }}
              onFocus={() => setSuggestOpen(true)}
              onBlur={() => setSuggestOpen(false)}
              onKeyDown={handleSearchKeyDown}
              placeholder="Введите адрес, город или ориентир"
              autoComplete="off"
            />
            {geo.loading && <span className="cm-spinner cm-spinner--small" />}
            {geo.query && !geo.loading && (
              <button
                type="button"
                className="cm-search__clear"
                onMouseDown={(e) => e.preventDefault()}
                onClick={geo.clear}
                aria-label="Очистить"
              >
                ×
              </button>
            )}
          </div>

          {showSuggestions && (
            <div className="cm-suggestions">
              {geo.loading && geo.suggestions.length === 0 && (
                <div className="cm-suggestions__hint">Ищем адрес…</div>
              )}
              {!geo.loading && geo.error && (
                <div className="cm-suggestions__hint">Не удалось выполнить поиск</div>
              )}
              {!geo.loading && !geo.error && geo.suggestions.length === 0 && (
                <div className="cm-suggestions__hint">Ничего не нашлось</div>
              )}
              {geo.suggestions.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  className="cm-suggestions__item"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => handleSelectSuggestion(s)}
                >
                  <span className="cm-suggestions__title">{formatAddress(s)}</span>
                  <span className="cm-suggestions__subtitle">{formatPlaceMeta(s.normalized)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {selected && (resolving || selected.label || selected.normalized) && (
        <div className="cm-address-card">
          {resolving ? (
            <span className="cm-address-card__hint">Определяю адрес…</span>
          ) : (
            <>
              <div className="cm-address-card__title">
                {selected.notFound ? selected.label : formatAddress(selected)}
              </div>
              {selected.normalized && (
                <div className="cm-address-card__meta">{formatPlaceMeta(selected.normalized)}</div>
              )}
              {selected.note && <div className="cm-address-card__note">{selected.note}</div>}
              {Number.isFinite(selected.lat) && (
                <div className="cm-address-card__coords">
                  {selected.lat.toFixed(6)}, {selected.lng.toFixed(6)}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
