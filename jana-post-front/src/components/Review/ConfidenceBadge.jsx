import { DEFAULT_THRESHOLD } from '../../address/fields';

function toneOf(record) {
  if (record.reviewed) return 'ok';
  if (record.confidence < DEFAULT_THRESHOLD) return 'low';
  return record.confidence < 0.85 ? 'mid' : 'high';
}

export default function ConfidenceBadge({ record, size = 'md' }) {
  const tone = toneOf(record);
  return (
    <span className={`conf conf--${tone} conf--${size}`} title="Уверенность разбора">
      {tone === 'ok' ? (
        <>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="m5 12.5 4.5 4.5L19 7.5" stroke="currentColor" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {size === 'lg' ? 'Проверено' : '100%'}
        </>
      ) : (
        `${Math.round(record.confidence * 100)}%`
      )}
    </span>
  );
}
