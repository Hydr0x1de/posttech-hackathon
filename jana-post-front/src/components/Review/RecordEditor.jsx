import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { FIELDS, STREET_TYPES } from '../../address/fields';
import { highlightRanges } from '../../address/records';
import ConfidenceBadge from './ConfidenceBadge';
import RawAddress from './RawAddress';

const KEYS = ['province', 'city', 'district', 'living_area', 'street_name', 'street_type', 'house', 'apartment', 'entrance', 'floor', 'landmark'];

const BY_KEY = Object.fromEntries(FIELDS.map((f) => [f.key, f]));

const EASE = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
const RISE_MS = 700;
const FIELD_STEP_MS = 35;
const FLY_MS = 560;
const FLY_STEP_MS = 120;

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

function draftOf(record) {
  return Object.fromEntries(FIELDS.map((f) => [f.key, record[f.key] ?? '']));
}

const fontSizeOf = (el) => parseFloat(getComputedStyle(el).fontSize) || 16;

export default function RecordEditor({ record, origin, onBack, onSave }) {
  const [draft, setDraft] = useState(() => draftOf(record));
  const [activeKey, setActiveKey] = useState(null);
  const [animating, setAnimating] = useState(() => !reducedMotion());
  const [revealed, setRevealed] = useState(() => new Set());
  const [litKeys, setLitKeys] = useState(() => new Set());

  const barRef = useRef(null);
  const rawRef = useRef(null);
  const stopRef = useRef(() => {});

  const preview = { ...record };
  for (const [key, value] of Object.entries(draft)) preview[key] = value.trim() || null;

  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === 'Escape') onBack();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onBack]);

  useLayoutEffect(() => {
    const bar = barRef.current;
    const workspace = bar?.closest('.workspace');
    if (!bar || !workspace) return undefined;
    const sync = () => workspace.style.setProperty('--bar-h', `${bar.offsetHeight}px`);
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(bar);
    return () => {
      observer.disconnect();
      workspace.style.removeProperty('--bar-h');
    };
  }, []);

  useLayoutEffect(() => {
    if (reducedMotion()) return undefined;
    const bar = barRef.current;
    const raw = rawRef.current;
    if (!bar || !raw) return undefined;
    const timers = [];
    const anims = [];
    const chips = [];
    const later = (fn, ms) => timers.push(setTimeout(fn, ms));
    const reveal = (key) => setRevealed((prev) => new Set(prev).add(key));
    const light = (key) => setLitKeys((prev) => new Set(prev).add(key));

    bar.classList.add('topbar--column');
    const items = [...bar.querySelectorAll('[data-flip]')];
    const first = items.map((el) => el.getBoundingClientRect());
    const barFirst = bar.getBoundingClientRect();
    const rawFirst = raw.getBoundingClientRect();
    const rawFont = fontSizeOf(raw);
    bar.classList.remove('topbar--column');
    const barLast = bar.getBoundingClientRect();

    const k = origin ? origin.fontSize / rawFont : 1;
    let fieldIndex = 0;
    let fieldsDoneAt = RISE_MS;
    items.forEach((el, i) => {
      const last = el.getBoundingClientRect();
      if (!last.width || !last.height) return;
      const kind = el.dataset.flip;
      let { left, top, width, height } = first[i];

      if (kind === 'word') {
        if (origin) {
          left = origin.left + (left - rawFirst.left) * k;
          top = origin.top + (top - rawFirst.top) * k;
          height *= k;
        }
        const s = height / last.height;
        anims.push(
          el.animate(
            [{ transform: `translate(${left - last.left}px, ${top - last.top}px) scale(${s})` }, { transform: 'none' }],
            { duration: RISE_MS, easing: EASE, delay: Math.min(i * 4, 60), fill: 'backwards' }
          )
        );
        return;
      }

      if (kind === 'field') {
        const delay = 160 + fieldIndex * FIELD_STEP_MS;
        fieldIndex += 1;
        fieldsDoneAt = Math.max(fieldsDoneAt, delay + RISE_MS);
        anims.push(
          el.animate(
            [
              { transform: `translate(${left - last.left}px, ${top - last.top}px)`, width: `${width}px`, opacity: 0 },
              { opacity: 1, offset: 0.25 },
              { transform: 'none', width: `${last.width}px`, opacity: 1 },
            ],
            { duration: RISE_MS, easing: EASE, delay, fill: 'backwards' }
          )
        );
        return;
      }

      anims.push(
        el.animate(
          [
            { transform: `translate(${left - last.left}px, ${top - last.top}px)`, opacity: 0 },
            { transform: 'none', opacity: 1 },
          ],
          { duration: RISE_MS, easing: EASE, delay: 120, fill: 'backwards' }
        )
      );
    });

    const bg = bar.querySelector('.topbar__bg');
    anims.push(
      bg.animate(
        [
          {
            top: `${barFirst.top - barLast.top}px`,
            left: `${barFirst.left - barLast.left}px`,
            width: `${barFirst.width}px`,
            height: `${barFirst.height}px`,
            borderRadius: '24px',
          },
          { top: '0px', left: '0px', width: `${barLast.width}px`, height: `${barLast.height}px`, borderRadius: '20px' },
        ],
        { duration: RISE_MS, easing: EASE }
      )
    );

    const fly = (key) => {
      const frag = raw.querySelector(`[data-key="${key}"]`);
      const input = bar.querySelector(`.field--${key} input`);
      if (!frag || !input) return reveal(key);

      const from = frag.getClientRects()[0] || frag.getBoundingClientRect();
      const to = input.getBoundingClientRect();
      if (to.bottom > window.innerHeight || to.top < 0) return reveal(key);

      light(key);
      const pad = 4;
      const s = fontSizeOf(input) / fontSizeOf(raw);
      const dx = to.left + 12 - pad * s - (from.left - pad);
      const dy = to.top + (to.height - from.height * s) / 2 - from.top;

      const chip = document.createElement('span');
      chip.className = 'fly-chip';
      chip.textContent = frag.textContent;
      chip.style.left = `${from.left - pad}px`;
      chip.style.top = `${from.top}px`;
      chip.style.height = `${from.height}px`;
      chip.style.lineHeight = `${from.height}px`;
      document.body.appendChild(chip);
      chips.push(chip);

      const flight = chip.animate(
        [
          { transform: 'translate(0, 0) scale(1)' },
          { transform: `translate(${dx * 0.5}px, ${dy * 0.5 - 22}px) scale(${(1 + s) / 2})`, offset: 0.45 },
          { transform: `translate(${dx}px, ${dy}px) scale(${s})` },
        ],
        { duration: FLY_MS, easing: EASE, fill: 'forwards' }
      );
      anims.push(flight);
      flight.onfinish = () => {
        reveal(key);
        const fade = chip.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, fill: 'forwards' });
        anims.push(fade);
        fade.onfinish = () => chip.remove();
      };
      return undefined;
    };

    const ranges = highlightRanges(record);
    const start = fieldsDoneAt + 80;
    ranges.forEach((range, i) => later(() => fly(range.key), start + i * FLY_STEP_MS));

    const autoAt = start + ranges.length * FLY_STEP_MS + FLY_MS;
    later(() => {
      for (const f of FIELDS) if (record[f.key]) reveal(f.key);
    }, autoAt);
    later(() => {
      setAnimating(false);
      setLitKeys(new Set());
    }, autoAt + 700);

    const stop = () => {
      timers.forEach(clearTimeout);
      anims.forEach((a) => a.cancel());
      chips.forEach((c) => c.remove());
    };
    stopRef.current = stop;
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const skipAnimation = () => {
    if (!animating) return;
    stopRef.current();
    setAnimating(false);
    setLitKeys(new Set());
  };

  const fieldState = (key) => {
    if (!animating) return '';
    return revealed.has(key) ? ' field--landed' : ' field--pending';
  };

  return (
    <form
      ref={barRef}
      className="topbar"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(draft);
      }}
    >
      <span className="topbar__bg" />
      <button type="button" className="icon-btn topbar__back" onClick={onBack} aria-label="К списку" data-flip="">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M15 5 8 12l7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      <span className="editor__title topbar__id" data-flip="">
        {record.id}
      </span>
      <span className="topbar__badge" data-flip="">
        <ConfidenceBadge record={record} size="lg" />
      </span>
      <div className="topbar__raw">
        <RawAddress record={preview} activeKey={activeKey} litKeys={litKeys} rawRef={rawRef} />
      </div>

      <div className="topbar__fields">
        {KEYS.map((key) => (
          <label key={key} className={`field field--${key}${fieldState(key)}`} data-flip="field">
            <span className="field__label">{BY_KEY[key].label}</span>
            <input
              value={draft[key]}
              list={key === 'street_type' ? 'street-types' : undefined}
              onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
              onFocus={() => {
                skipAnimation();
                setActiveKey(key);
              }}
              onBlur={() => setActiveKey(null)}
              title={BY_KEY[key].hint}
              spellCheck={false}
            />
          </label>
        ))}
        <button type="submit" className="btn btn--primary topbar__save" data-flip="field">
          Сохранить
        </button>
      </div>
      <datalist id="street-types">
        {STREET_TYPES.map((t) => (
          <option key={t} value={t} />
        ))}
      </datalist>
    </form>
  );
}
