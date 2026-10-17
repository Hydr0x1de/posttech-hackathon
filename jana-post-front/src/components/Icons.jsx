function Icon({ children, size = 24, strokeWidth = 1.8, ...rest }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const FileIcon = (p) => (
  <Icon {...p}>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5ZM14 3v5h5M10 13l-1.5 1.5L10 16M14 13l1.5 1.5L14 16" />
  </Icon>
);

export const CheckIcon = (p) => (
  <Icon {...p}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Icon>
);

export const DownloadIcon = (p) => (
  <Icon {...p}>
    <path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 20h14" />
  </Icon>
);

export const UploadFileGlyph = ({ size = 52 }) => (
  <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
    <defs>
      <linearGradient id="upload-glyph-fill" x1="10" y1="4" x2="38" y2="44" gradientUnits="userSpaceOnUse">
        <stop offset="0" stopColor="#4cc75a" />
        <stop offset="1" stopColor="#178a2d" />
      </linearGradient>
    </defs>
    <path d="M13 4h15.5L39 14.5V40a4 4 0 0 1-4 4H13a4 4 0 0 1-4-4V8a4 4 0 0 1 4-4Z" fill="url(#upload-glyph-fill)" />
    <path d="M28.5 4v7.5a3 3 0 0 0 3 3H39Z" fill="#fff" fillOpacity="0.45" />
    <path
      d="M24 34V25m-4 4 4-4 4 4"
      fill="none"
      stroke="#fff"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);
