import { useNavigate } from 'react-router-dom';
import { ArrowLeft, RefreshCw } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { SystemStatusPanel } from '@/components/settings/SystemStatusPanel';
import { ConfigChecklistPanel } from '@/components/settings/ConfigChecklistPanel';

export function SystemStatusPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const handleRefresh = () => {
    queryClient.invalidateQueries({ queryKey: ['settings', 'system-status'] });
    queryClient.invalidateQueries({ queryKey: ['settings', 'config-checklist'] });
  };

  return (
    <div className="flex h-screen flex-col pb-20">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/settings')}
              aria-label="Back to settings"
              className="text-foreground/50 hover:text-foreground"
            >
              <ArrowLeft size={20} aria-hidden="true" />
            </button>
            <h1 className="text-lg font-bold text-foreground">System Status</h1>
          </div>
          <button
            onClick={handleRefresh}
            aria-label="Refresh status"
            className="flex items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-primary/10"
          >
            <RefreshCw size={13} aria-hidden="true" />
            Refresh
          </button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-3 pt-3 space-y-4">
        {/* System status card */}
        <div className="mb-2 space-y-1">
          <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-foreground/50">
            Live Status
          </h2>
          <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
            <SystemStatusPanel />
          </div>
        </div>

        {/* Config checklist card */}
        <div className="mb-4 space-y-1">
          <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-foreground/50">
            Configuration Checklist
          </h2>
          <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
            <ConfigChecklistPanel />
          </div>
        </div>
      </main>
    </div>
  );
}
