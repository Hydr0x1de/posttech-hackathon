import { useEffect, useState } from 'react';
import { FIELDS, STREET_TYPES } from '../../address/fields';
import ConfidenceBadge from './ConfidenceBadge';
import RawAddress from './RawAddress';

const GROUPS = [
  { id: 'address', title: 'Адрес', keys: ['province', 'city', 'district', 'living_area', 'street_name', 'street_type', 'house'] },
  { id: 'courier', title: 'Для курьера', keys: ['apartment', 'entrance', 'floor', 'landmark'] },
];

const BY_KEY = Object.fromEntries(FIELDS.map((f) => [f.key, f]));

function draftOf(record) {
  return Object.fromEntries(FIELDS.map((f) => [f.key, record[f.key] ?? '']));
}

export default function RecordEditor({ record, onBack, onSave }) {
  const [draft, setDraft] = useState(() => draftOf(record));
  const [activeKey, setActiveKey] = useState(null);

  const preview = { ...record };
  for (const [key, value] of Object.entries(draft)) preview[key] = value.trim() || null;

  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === 'Escape') onBack();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onBack]);

  return (
    <form
      className="editor"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(draft);
      }}
    >
      <div className="editor__body">
        <section className="pblock">
          <div className="pblock__row editor__head">
            <button type="button" className="icon-btn" onClick={onBack} aria-label="К списку">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M15 5 8 12l7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <span className="editor__title">{record.id}</span>
            <ConfidenceBadge record={record} size="lg" />
          </div>
          <RawAddress record={preview} activeKey={activeKey} />
        </section>

        {GROUPS.map((group) => (
          <section key={group.id} className="pblock">
            <b className="pblock__title">{group.title}</b>
            <div className={`fields fields--${group.id}`}>
              {group.keys.map((key) => (
                <label key={key} className={`field field--${key}`}>
                  <span className="field__label">{BY_KEY[key].label}</span>
                  <input
                    value={draft[key]}
                    list={key === 'street_type' ? 'street-types' : undefined}
                    onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                    onFocus={() => setActiveKey(key)}
                    onBlur={() => setActiveKey(null)}
                    title={BY_KEY[key].hint}
                    spellCheck={false}
                  />
                </label>
              ))}
            </div>
          </section>
        ))}
        <datalist id="street-types">
          {STREET_TYPES.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
      </div>

      <footer className="pblock editor__foot">
        <button type="submit" className="btn btn--primary editor__save">
          Сохранить
        </button>
      </footer>
    </form>
  );
}
