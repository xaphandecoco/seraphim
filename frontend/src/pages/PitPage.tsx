import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { AlertTriangle, UserCheck, Trash2, UserX, ArrowLeft } from 'lucide-react';
import { api } from '@/services/api';
import { useNavigate } from 'react-router-dom';
import type { Task } from '@/types';

async function fetchPitTasks(): Promise<Task[]> {
  const res = await api.get('/pit');
  return res.data;
}

export function PitPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [actionLoading, setActionLoading] = useState<number | null>(null);

  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ['pit'],
    queryFn: fetchPitTasks,
  });

  const handleAction = async (taskId: number, action: 'enroll' | 'delete' | 'non-person') => {
    setActionLoading(taskId);
    try {
      await api.post(`/pit/${taskId}/${action}`);
      queryClient.invalidateQueries({ queryKey: ['pit'] });
      toast.success(`Action "${action}" applied`);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || `Failed to ${action} task`);
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="flex h-screen flex-col pb-20">
      <header className="border-b border-border bg-white/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/')} className="text-foreground/50 hover:text-foreground">
            <ArrowLeft size={20} />
          </button>
          <h1 className="text-lg font-bold text-foreground">Admin Pit</h1>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {isLoading ? (
          <div className="py-16 text-center text-sm text-foreground/50">Loading...</div>
        ) : tasks.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-foreground/50">
            <AlertTriangle size={40} className="mb-3 opacity-40" />
            <p className="text-sm font-medium">Pit queue is empty</p>
            <p className="mt-1 text-xs">Tasks that hit 4 skips appear here</p>
          </div>
        ) : (
          <div className="space-y-3 pb-4">
            {tasks.map((task) => (
              <div key={task.id} className="rounded-2xl border border-border bg-card p-4 shadow-sm">
                <div className="flex gap-3">
                  {task.face_thumbnail_path ? (
                    <img
                      src={`/api${task.face_thumbnail_path}`}
                      alt="Face"
                      className="h-20 w-20 rounded-xl object-cover"
                      loading="lazy"
                    />
                  ) : (
                    <div className="flex h-20 w-20 items-center justify-center rounded-xl bg-background">
                      <span className="text-xs text-foreground/40">No image</span>
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-bold text-foreground">
                      {task.matched_name || 'Unknown'}
                    </p>
                    <p className="text-xs text-foreground/50">
                      Skipped {task.skip_count} times
                    </p>
                    <p className="text-xs text-foreground/50">
                      {task.camera_name}
                    </p>
                  </div>
                </div>

                <div className="mt-3 grid grid-cols-3 gap-2">
                  <button
                    onClick={() => handleAction(task.id, 'enroll')}
                    disabled={actionLoading === task.id}
                    className="flex min-h-[44px] items-center justify-center gap-1 rounded-xl bg-green-50 text-sm font-semibold text-green-700 border border-green-200 transition-all hover:bg-green-100 active:scale-[0.98] disabled:opacity-50"
                  >
                    <UserCheck size={16} />
                    <span className="text-xs">Enroll</span>
                  </button>
                  <button
                    onClick={() => handleAction(task.id, 'non-person')}
                    disabled={actionLoading === task.id}
                    className="flex min-h-[44px] items-center justify-center gap-1 rounded-xl bg-amber-50 text-sm font-semibold text-amber-700 border border-amber-200 transition-all hover:bg-amber-100 active:scale-[0.98] disabled:opacity-50"
                  >
                    <UserX size={16} />
                    <span className="text-xs">Non-Person</span>
                  </button>
                  <button
                    onClick={() => handleAction(task.id, 'delete')}
                    disabled={actionLoading === task.id}
                    className="flex min-h-[44px] items-center justify-center gap-1 rounded-xl bg-red-50 text-sm font-semibold text-red-600 border border-red-200 transition-all hover:bg-red-100 active:scale-[0.98] disabled:opacity-50"
                  >
                    <Trash2 size={16} />
                    <span className="text-xs">Delete</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
