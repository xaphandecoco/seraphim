import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { CheckCircle2, XCircle, Pencil, ShieldCheck } from 'lucide-react';
import { api } from '@/services/api';
import { ContactPickerModal } from '@/components/tasks/ContactPickerModal';
import type { Member } from '@/types';

interface AuditTask {
  detection_id: number;
  face_thumbnail_path: string | null;
  matched_name: string | null;
  confidence: number | null;
  tier: string | null;
  camera_name: string | null;
  detected_at: string | null;
  contact_id: number | null;
}

async function fetchAuditTasks(): Promise<AuditTask[]> {
  const res = await api.get('/audit/tasks');
  return res.data;
}

export function AuditPage() {
  const queryClient = useQueryClient();
  const [loading, setLoading] = useState<number | null>(null);
  const [editingTask, setEditingTask] = useState<AuditTask | null>(null);

  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ['audit-tasks'],
    queryFn: fetchAuditTasks,
  });

  const act = async (detectionId: number, action: 'confirm' | 'deny', contactId?: number) => {
    setLoading(detectionId);
    try {
      const url = `/audit/${detectionId}/${action}`;
      await api.post(url, contactId ? { contact_id: contactId } : {});
      toast.success(action === 'confirm' ? 'Marked as correct' : 'Flagged as incorrect');
      queryClient.invalidateQueries({ queryKey: ['audit-tasks'] });
    } catch (err: any) {
      toast.error(err.response?.data?.detail || `Audit action failed`);
    } finally {
      setLoading(null);
    }
  };

  const handleMemberSelect = async (member: Member) => {
    if (!editingTask) return;
    setLoading(editingTask.detection_id);
    try {
      await api.post(`/audit/${editingTask.detection_id}/change`, { contact_id: member.contact_id });
      toast.success('Updated to ' + (member.display_name || `${member.first_name} ${member.last_name}`));
      queryClient.invalidateQueries({ queryKey: ['audit-tasks'] });
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Change failed');
    } finally {
      setLoading(null);
      setEditingTask(null);
    }
  };

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-2">
          <ShieldCheck size={18} className="text-primary" aria-hidden="true" />
          <h1 className="text-lg font-bold text-foreground">Quality Audit</h1>
        </div>
        <p className="mt-0.5 text-xs text-foreground/50">Verify accuracy of enrolled face matches</p>
      </header>

      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {isLoading ? (
          <div className="py-16 text-center text-sm text-foreground/50">Loading…</div>
        ) : tasks.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-foreground/50">
            <ShieldCheck size={40} className="mb-3 opacity-40" aria-hidden="true" />
            <p className="text-sm font-medium">No audit tasks right now</p>
            <p className="mt-1 text-xs">Audit tasks appear when the main queue is empty</p>
          </div>
        ) : (
          <div className="space-y-3 pb-4">
            {tasks.map((task) => (
              <div key={task.detection_id} className="rounded-2xl border border-border bg-card p-4 shadow-sm">
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
                    <p className="text-sm font-bold text-foreground">{task.matched_name || 'Unknown'}</p>
                    {task.confidence !== null && (
                      <p className="text-xs text-foreground/50">
                        {(task.confidence * 100).toFixed(1)}% confidence
                      </p>
                    )}
                    <p className="text-xs text-foreground/50">{task.camera_name}</p>
                    {task.detected_at && (
                      <p className="text-xs text-foreground/50">{new Date(task.detected_at).toLocaleString()}</p>
                    )}
                  </div>
                </div>

                <div className="mt-3 grid grid-cols-3 gap-2">
                  <button
                    onClick={() => act(task.detection_id, 'confirm')}
                    disabled={loading === task.detection_id}
                    className="flex min-h-[44px] items-center justify-center gap-1 rounded-xl border border-green-200 bg-green-50 text-xs font-semibold text-green-700 transition-all hover:bg-green-100 disabled:opacity-50"
                  >
                    <CheckCircle2 size={15} aria-hidden="true" />
                    Correct
                  </button>
                  <button
                    onClick={() => act(task.detection_id, 'deny')}
                    disabled={loading === task.detection_id}
                    className="flex min-h-[44px] items-center justify-center gap-1 rounded-xl border border-red-200 bg-red-50 text-xs font-semibold text-red-600 transition-all hover:bg-red-100 disabled:opacity-50"
                  >
                    <XCircle size={15} aria-hidden="true" />
                    Wrong
                  </button>
                  <button
                    onClick={() => setEditingTask(task)}
                    disabled={loading === task.detection_id}
                    className="flex min-h-[44px] items-center justify-center gap-1 rounded-xl border border-border bg-background text-xs font-semibold text-foreground transition-all hover:bg-primary/10 disabled:opacity-50"
                  >
                    <Pencil size={15} aria-hidden="true" />
                    Change
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      {editingTask && (
        <ContactPickerModal
          mode="edit"
          onSelect={handleMemberSelect}
          onClose={() => setEditingTask(null)}
        />
      )}
    </div>
  );
}
