import { ArrowLeft } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { EmptyState } from '@/components/ui/StateViews';

export function AttendancePage() {
  const navigate = useNavigate();
  return (
    <div className="flex h-screen flex-col pb-20">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/settings')}
            aria-label="Back"
            className="text-foreground/50 hover:text-foreground"
          >
            <ArrowLeft size={20} aria-hidden="true" />
          </button>
          <h1 className="text-lg font-bold text-foreground">Attendance</h1>
        </div>
      </header>
      <main className="flex flex-1 items-center justify-center px-4">
        <EmptyState
          title="Participant management coming soon"
          description="Use the Event detail page to view and manage attendance records."
        />
      </main>
    </div>
  );
}
