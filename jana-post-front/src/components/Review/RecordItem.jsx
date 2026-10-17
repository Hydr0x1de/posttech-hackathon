import { memo } from 'react';
import { parsedSummary } from '../../address/records';
import ConfidenceBadge from './ConfidenceBadge';

function RecordItem({ record, onOpen }) {
  const summary = parsedSummary(record);
  return (
    <button type="button" className="item" onClick={() => onOpen(record.id)}>
      <span className="item__main">
        <span className="item__raw">{record.raw_address}</span>
        <span className={`item__parsed${summary ? '' : ' item__parsed--empty'}`}>{summary || 'Не удалось разобрать'}</span>
      </span>
      <ConfidenceBadge record={record} />
    </button>
  );
}

export default memo(RecordItem);
