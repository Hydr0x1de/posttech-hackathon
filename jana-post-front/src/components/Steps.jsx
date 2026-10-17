import './Steps.css';

export default function Steps({ items, compact = false }) {
  return (
    <ol className={`steps${compact ? ' steps--compact' : ''}`}>
      {items.map(([title, text], index) => (
        <li key={title} className="steps__item">
          <span className="steps__num">{index + 1}</span>
          <span className="steps__text">
            <b>{title}</b>
            <span>{text}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}
