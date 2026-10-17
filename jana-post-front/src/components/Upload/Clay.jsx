import './Clay.css';

export function ClayCoin({ children, className = '' }) {
  return (
    <span className={`clay-coin ${className}`} aria-hidden="true">
      <span className="clay-coin__face">{children}</span>
    </span>
  );
}

export function ClayPin({ className = '' }) {
  return (
    <span className={`clay-pin ${className}`} aria-hidden="true">
      <span className="clay-pin__hole" />
    </span>
  );
}

export function ClayDoc({ className = '' }) {
  return (
    <span className={`clay-doc ${className}`} aria-hidden="true">
      <span className="clay-doc__fold" />
      <span className="clay-doc__mark">{'{ }'}</span>
      <span className="clay-doc__line" />
      <span className="clay-doc__line clay-doc__line--short" />
    </span>
  );
}
