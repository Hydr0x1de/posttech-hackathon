import { memo } from 'react';
import { parsedSummary } from '../../address/records';
import ConfidenceBadge from './ConfidenceBadge';

function RecordItem({ record, onOpen }) {
  const summary = parsedSummary(record);
  return (
    <button
      type="button"
      className="item"
      onClick={(e) => {
        const raw = e.currentTarget.querySelector('.item__raw');
        const rect = raw.getBoundingClientRect();
        onOpen(record.id, { left: rect.left, top: rect.top, fontSize: parseFloat(getComputedStyle(raw).fontSize) });
      }}
    >
      <span className="item__main">
        <span className="item__raw">{record.raw_address}</span>
        <span className={`item__parsed${summary ? '' : ' item__parsed--empty'}`}>{summary || 'Не удалось разобрать'}</span>
      </span>
      <ConfidenceBadge record={record} />
    </button>
  );
}

export default memo(RecordItem);
