import { downloadJson, toOutput } from './address/records';
import Workspace from './components/Review/Workspace';
import UploadScreen from './components/Upload/UploadScreen';
import { useReviewSession } from './hooks/useReviewSession';
import { useTheme } from './hooks/useTheme';

export default function App() {
  const session = useReviewSession();
  const { theme, toggle } = useTheme();

  if (session.status !== 'ready') {
    return (
      <UploadScreen
        status={session.status}
        progress={session.progress}
        error={session.error}
        theme={theme}
        onToggleTheme={toggle}
        onRun={session.run}
        onCancel={session.cancel}
      />
    );
  }

  const handleExport = () => {
    downloadJson('sample_output.json', session.records.map(toOutput));
  };

  return (
    <Workspace
      records={session.records}
      fileName={session.fileName}
      theme={theme}
      onToggleTheme={toggle}
      onSave={session.saveRecord}
      onExport={handleExport}
      onReset={() => {
        if (window.confirm('Загрузить другой файл? Текущий разбор и правки будут сброшены.')) session.reset();
      }}
    />
  );
}
