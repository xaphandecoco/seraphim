import { useEffect, useState } from 'react';
import { useTaskStore } from '@/store/taskStore';
import { TaskFeed } from '@/components/tasks/TaskFeed';
import { api } from '@/services/api';
import { AlertTriangle, ShieldAlert } from 'lucide-react';

interface QueueStatus {
  pending_count: number;
  hard_limit: number;
  resume_limit: number;
  saturated: boolean;
  paused: boolean;
}

export function TasksPage() {
  const pendingCount = useTaskStore((s) => s.pendingCount);
  const setPendingCount = useTaskStore((s) => s.setPendingCount);
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null);
  const [safeMode, setSafeMode] = useState(false);

  useEffect(() => {
    const checkStatus = async () => {
      try {
        const [queueRes, settingsRes] = await Promise.all([
          api.get('/health/queue'),
          api.get('/settings').catch(() => null),
        ]);
        setQueueStatus(queueRes.data);
        setPendingCount(queueRes.data.pending_count);
        if (settingsRes) {
          const safeSetting = settingsRes.data.settings?.find((s: any) => s.key === 'safe_mode');
          setSafeMode(safeSetting?.value?.value === true);
        }
      } catch {
        // silent
      }
    };

    checkStatus();
    const interval = setInterval(checkStatus, 10000);
    return () => clearInterval(interval);
  }, [setPendingCount]);

  return (
    <div className="flex h-screen flex-col">
      {/* Safe Mode Banner */}
      {safeMode && (
        <div className="flex items-center justify-center gap-2 bg-amber-50 px-4 py-2 text-xs font-bold text-amber-700 border-b border-amber-200">
          <ShieldAlert size={14} />
          <span>System in Safe Mode — recognition paused</span>
        </div>
      )}

      {/* Queue Saturated Banner */}
      {queueStatus?.saturated && (
        <div className="flex items-center justify-center gap-2 bg-red-50 px-4 py-2 text-xs font-bold text-red-600 border-b border-red-200">
          <AlertTriangle size={14} />
          <span>Queue Saturated ({queueStatus.pending_count}/{queueStatus.hard_limit}) — ingestion paused</span>
        </div>
      )}

      <header className="border-b border-[#E8DDA8] bg-white/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-bold text-[#1F2128]">Tasks</h1>
          {pendingCount > 0 && (
            <span className="rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-bold text-red-600">
              {pendingCount} pending
            </span>
          )}
        </div>
      </header>
      <main className="flex-1 overflow-hidden">
        <TaskFeed />
      </main>
    </div>
  );
}
