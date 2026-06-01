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
  safe_mode: boolean;
}

export function TasksPage() {
  const setPendingCount = useTaskStore((s) => s.setPendingCount);
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null);

  useEffect(() => {
    const checkStatus = async () => {
      try {
        const queueRes = await api.get<QueueStatus>('/health/queue');
        setQueueStatus(queueRes.data);
        setPendingCount(queueRes.data.pending_count);
      } catch {
        // silent — queue status is informational
      }
    };

    checkStatus();
    const interval = setInterval(checkStatus, 10000);
    return () => clearInterval(interval);
  }, [setPendingCount]);

  return (
    <div className="flex h-screen flex-col">
      {/* Safe Mode Banner — visible to all users */}
      {queueStatus?.safe_mode && (
        <div className="flex items-center justify-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs font-bold text-amber-700">
          <ShieldAlert size={14} aria-hidden="true" />
          <span>System in Safe Mode — recognition paused</span>
        </div>
      )}

      {/* Queue Saturated Banner */}
      {queueStatus?.saturated && (
        <div className="flex items-center justify-center gap-2 border-b border-red-200 bg-red-50 px-4 py-2 text-xs font-bold text-red-600">
          <AlertTriangle size={14} aria-hidden="true" />
          <span>Queue Saturated ({queueStatus.pending_count}/{queueStatus.hard_limit}) — ingestion paused</span>
        </div>
      )}

      <header className="border-b border-border bg-white/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-bold text-foreground">Tasks</h1>
          {queueStatus && queueStatus.pending_count > 0 && (
            <span className="rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-bold text-red-600">
              {queueStatus.pending_count} pending
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
