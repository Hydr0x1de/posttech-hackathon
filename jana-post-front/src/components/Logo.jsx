import './Logo.css';

export default function Logo() {
  return (
    <span className="logo" aria-label="Jana Address">
      <span className="logo__jana" aria-hidden="true">
        JAŃA
      </span>
      <span className="logo__product" aria-hidden="true">
        Address
      </span>
    </span>
  );
}
