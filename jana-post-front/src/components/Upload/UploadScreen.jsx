import { useRef, useState } from 'react';
import { linesToRecords, parseInputFile } from '../../address/records';
import { ArrowRightIcon, CheckIcon, DownloadIcon, FileIcon, MapPinIcon, UploadFileGlyph } from '../Icons';
import Logo from '../Logo';
import ThemeToggle from '../ThemeToggle';
import { ClayCoin, ClayDoc, ClayPin } from './Clay';
import './UploadScreen.css';

const STEPS = [
  { tone: 'dark', Icon: FileIcon, title: 'Загрузите файл', text: 'JSON как sample_input' },
  { tone: 'mint', Icon: CheckIcon, title: 'Поправьте неуверенные', text: 'они отмечены красным' },
  { tone: 'forest', Icon: DownloadIcon, title: 'Скачайте результат', text: 'JSON как sample_output' },
];

export default function UploadScreen({ status, progress, error, theme, onToggleTheme, onRun, onCancel }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [text, setText] = useState('');
  const [address, setAddress] = useState('');
  const [localError, setLocalError] = useState(null);

  const parsing = status === 'parsing';
  const shownError = localError || (status === 'error' ? error : null);

  const handleFile = async (file) => {
    if (!file) return;
    setLocalError(null);
    try {
      onRun(parseInputFile(await file.text()), file.name);
    } catch (err) {
      setLocalError(err.message);
    }
  };

  const handleText = () => {
    const records = linesToRecords(text);
    if (records.length === 0) return;
    setLocalError(null);
    onRun(records, 'Ручной ввод');
  };

  const handleAddress = (e) => {
    e.preventDefault();
    const records = linesToRecords(address);
    if (records.length === 0) return;
    setLocalError(null);
    onRun(records, 'Ручной ввод');
  };

  return (
    <div className="landing">
      <header className="landing__header">
        <Logo />
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
      </header>

      <main className="bento">
        <section className="tile tile--upload">
          <ClayDoc className="upload__doc" />
          <ClayPin className="upload__pin" />

          <div className="upload__content">
            <h1>Разбор адресов доставки</h1>
            <p className="upload__lead">Разложим каждую строку на город, улицу, дом, квартиру и&nbsp;подъезд</p>

            {parsing ? (
              <div className="dropzone dropzone--busy" aria-live="polite">
                <b>Разбираем… {Math.round(progress * 100)}%</b>
                <div className="bar">
                  <div className="bar__fill" style={{ width: `${Math.max(3, progress * 100)}%` }} />
                </div>
                <button type="button" className="btn btn--light" onClick={onCancel}>
                  Отменить
                </button>
              </div>
            ) : manualOpen ? (
              <div className="dropzone dropzone--manual">
                <textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="Один адрес на строку"
                  rows={6}
                  autoFocus
                />
                <div className="dropzone__actions">
                  <button type="button" className="btn btn--dark btn--lg" onClick={handleText} disabled={!text.trim()}>
                    Разобрать
                  </button>
                  <button type="button" className="btn btn--light btn--lg" onClick={() => setManualOpen(false)}>
                    Загрузить файл
                  </button>
                </div>
              </div>
            ) : (
              <>
              <form className="address-bar" onSubmit={handleAddress}>
                <MapPinIcon className="address-bar__pin" size={22} strokeWidth={2.2} />
                <input
                  type="text"
                  value={address}
                  onChange={(e) => setAddress(e.target.value)}
                  onPaste={(e) => {
                    const pasted = e.clipboardData.getData('text');
                    if (!pasted.includes('\n')) return;
                    e.preventDefault();
                    setText(address ? `${address}\n${pasted}` : pasted);
                    setAddress('');
                    setManualOpen(true);
                  }}
                  placeholder="Адрес, например Туран 55/6"
                  aria-label="Адрес доставки"
                />
                <button type="submit" className="address-bar__go" disabled={!address.trim()} aria-label="Разобрать адрес">
                  <ArrowRightIcon size={20} strokeWidth={2.2} />
                </button>
              </form>

              <div className="divider" aria-hidden="true">
                <span>или</span>
              </div>

              <div
                className={`dropzone${dragging ? ' dropzone--active' : ''}`}
                role="button"
                tabIndex={0}
                onClick={() => inputRef.current?.click()}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    inputRef.current?.click();
                  }
                }}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  handleFile(e.dataTransfer.files[0]);
                }}
              >
                <span className="dropzone__icon">
                  <UploadFileGlyph />
                </span>
                <span className="dropzone__text">
                  <b>{dragging ? 'Отпустите файл' : 'Перетащите файл сюда'}</b>
                  <span>JSON в формате sample_input</span>
                </span>
                <span className="btn btn--primary btn--lg dropzone__btn">Выбрать файл</span>
              </div>
              </>
            )}

            {shownError && (
              <div className="alert" role="alert">
                {shownError}
              </div>
            )}
          </div>

          <input
            ref={inputRef}
            type="file"
            accept=".json,application/json"
            hidden
            onChange={(e) => {
              handleFile(e.target.files[0]);
              e.target.value = '';
            }}
          />
        </section>

        {STEPS.map(({ tone, Icon, title, text: sub }, index) => (
          <section key={title} className={`tile tile--step tile--${tone}`}>
            <ClayCoin className="step__coin">
              <Icon size={72} strokeWidth={2.4} />
            </ClayCoin>
            <span className="step__num">{String(index + 1).padStart(2, '0')}</span>
            <div className="step__text">
              <b>{title}</b>
              <span>{sub}</span>
            </div>
          </section>
        ))}
      </main>
    </div>
  );
}
