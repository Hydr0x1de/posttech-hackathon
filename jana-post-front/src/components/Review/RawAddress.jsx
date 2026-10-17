import { highlightRanges } from '../../address/records';

export default function RawAddress({ record, activeKey }) {
  const raw = record.raw_address;
  const range = activeKey ? highlightRanges(record).find((r) => r.key === activeKey) : null;
  if (!range) return <p className="raw">{raw}</p>;
  return (
    <p className="raw">
      {raw.slice(0, range.start)}
      <mark>{raw.slice(range.start, range.end)}</mark>
      {raw.slice(range.end)}
    </p>
  );
}
