import { highlightRanges } from '../../address/records';

function words(text, prefix) {
  return text.split(/(\s+)/).map((part, i) =>
    !part || /^\s+$/.test(part) ? (
      part
    ) : (
      <span key={`${prefix}-${i}`} className="raw__w" data-flip="word">
        {part}
      </span>
    )
  );
}

export default function RawAddress({ record, activeKey, litKeys, rawRef }) {
  const raw = record.raw_address;
  const parts = [];
  let pos = 0;
  for (const range of highlightRanges(record)) {
    if (range.start > pos) parts.push(...words(raw.slice(pos, range.start), pos));
    const active = range.key === activeKey ? ' raw__frag--active' : '';
    const lit = litKeys?.has(range.key) ? ' raw__frag--lit' : '';
    parts.push(
      <span key={range.key} data-key={range.key} className={`raw__frag${active}${lit}`}>
        {words(raw.slice(range.start, range.end), range.start)}
      </span>
    );
    pos = range.end;
  }
  parts.push(...words(raw.slice(pos), pos));

  return (
    <p ref={rawRef} className="raw">
      {parts}
    </p>
  );
}
