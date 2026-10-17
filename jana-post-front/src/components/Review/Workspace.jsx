import { useEffect, useMemo, useRef, useState } from 'react';
import { DEFAULT_THRESHOLD, FIELDS } from '../../address/fields';
import { mapQueries } from '../../address/records';
import CustomMap from '../CustomMap/CustomMap';
import { createNominatimProvider } from '../CustomMap/geocodeProviders';
import Logo from '../Logo';
import ThemeToggle from '../ThemeToggle';
import Steps from '../Steps';
import RecordEditor from './RecordEditor';
import RecordItem from './RecordItem';
import './Workspace.css';

const PAGE = 100;
const KAZAKHSTAN = [67, 48];

const isLow = (r) => r.confidence < DEFAULT_THRESHOLD;

const TIP_KEY = 'jana-address-tip-hidden';
const TIP_STEPS = [
  ['Откройте «Неуверенные»', 'там адреса, где разбор мог ошибиться'],
  ['Сверьте поля со строкой', 'при клике на поле его фрагмент подсветится'],
  ['Сохраните и скачайте JSON', 'проверенные записи получат уверенность 100%'],
];

function readTipOpen() {
  try {
    return window.localStorage.getItem(TIP_KEY) !== '1';
  } catch {
    return true;
  }
}

function matchesQuery(record, q) {
  if (!q) return true;
  const has = (s) => s?.toLocaleLowerCase('ru').includes(q);
  return has(record.id) || has(record.raw_address) || FIELDS.some((f) => has(record[f.key]));
}

export default function Workspace({ records, fileName, theme, onToggleTheme, onSave, onExport, onReset }) {
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [openId, setOpenId] = useState(null);
  const [limit, setLimit] = useState(PAGE);
  const [tipOpen, setTipOpen] = useState(readTipOpen);

  const hideTip = () => {
    setTipOpen(false);
    try {
      window.localStorage.setItem(TIP_KEY, '1');
    } catch {}
  };

  const changeFilter = (next) => {
    setFilter(next);
    setLimit(PAGE);
  };
  const sentinelRef = useRef(null);
  const geocoder = useMemo(() => createNominatimProvider({ language: 'ru', countryCodes: 'kz' }), []);

  const lowCount = useMemo(() => records.filter(isLow).length, [records]);

  const visible = useMemo(() => {
    const q = query.trim().toLocaleLowerCase('ru');
    return records.filter((r) => (filter === 'low' ? isLow(r) : true) && matchesQuery(r, q));
  }, [records, filter, query]);

  const open = openId ? records.find((r) => r.id === openId) : null;
  const shown = visible.slice(0, limit);

  useEffect(() => {
    const node = sentinelRef.current;
    if (!node) return undefined;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) setLimit((l) => l + PAGE);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [shown.length, visible.length, open]);

  const handleSave = (values) => {
    const index = visible.findIndex((r) => r.id === open.id);
    const next = visible[index + 1] ?? null;
    onSave(open.id, values);
    setOpenId(next?.id ?? null);
  };

  const hasCoords = open && Number.isFinite(open.lat) && Number.isFinite(open.lng);

  return (
    <div className="workspace">
      <div className="workspace__map">
        <CustomMap
          key={theme}
          geocoder={geocoder}
          initialCenter={KAZAKHSTAN}
          initialZoom={5}
          defaultStyleId={theme === 'dark' ? 'carto-dark' : 'carto-voyager'}
          offlineStyleId={theme === 'dark' ? 'offline-dark' : 'offline-light'}
          focusQueries={open && !hasCoords ? mapQueries(open) : undefined}
          focusPoint={hasCoords ? { lat: open.lat, lng: open.lng, label: open.raw_address } : undefined}
        />
      </div>

      <aside className="panel">
        {open ? (
          <RecordEditor key={open.id} record={open} onBack={() => setOpenId(null)} onSave={handleSave} />
        ) : (
          <>
            <section className="pblock pblock--head">
              <div className="pblock__row">
                <Logo />
                <span className="panel__spacer" />
                <button type="button" className="icon-btn" onClick={onReset} title="Загрузить другой файл">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                </button>
                <button type="button" className="btn btn--primary" onClick={onExport}>
                  Скачать JSON
                </button>
              </div>
              <div className="pblock__row">
                <span className="panel__file">
                  {fileName} · {records.length} адр.
                </span>
                <ThemeToggle theme={theme} onToggle={onToggleTheme} />
              </div>
            </section>

            {tipOpen && (
              <section className="pblock pblock--tip">
                <div className="pblock__row">
                  <b className="pblock__title">Как проверить разбор</b>
                  <button type="button" className="icon-btn icon-btn--sm" onClick={hideTip} aria-label="Скрыть подсказку">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                      <path d="M6 6l12 12M18 6 6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                    </svg>
                  </button>
                </div>
                <Steps items={TIP_STEPS} compact />
              </section>
            )}

            <section className="pblock pblock--list">
              <div className="panel__controls">
                <div className="tabs" role="tablist">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={filter === 'all'}
                    className={`tabs__item${filter === 'all' ? ' tabs__item--active' : ''}`}
                    onClick={() => changeFilter('all')}
                  >
                    Все
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={filter === 'low'}
                    className={`tabs__item${filter === 'low' ? ' tabs__item--active' : ''}`}
                    onClick={() => changeFilter('low')}
                  >
                    Неуверенные {lowCount > 0 && <span className="tabs__badge">{lowCount}</span>}
                  </button>
                </div>
                <input
                  className="panel__search"
                  type="search"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setLimit(PAGE);
                  }}
                  placeholder="Найти адрес"
                />
              </div>

              <div className="panel__list">
                {visible.length === 0 && (
                  <div className="panel__empty">
                    {filter === 'low' && !query ? 'Неуверенных адресов нет' : 'Ничего не найдено'}
                  </div>
                )}
                {shown.map((r) => (
                  <RecordItem key={r.id} record={r} onOpen={setOpenId} />
                ))}
                {shown.length < visible.length && <div ref={sentinelRef} className="panel__sentinel" />}
              </div>
            </section>
          </>
        )}
      </aside>
    </div>
  );
}
