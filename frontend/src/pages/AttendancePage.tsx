import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft, Send, Eye, RotateCcw, AlertCircle } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/services/api';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';

interface AttendanceRecord {
  id: number;
  contact_id: number | null;
  event_id: number | null;
  detection_id: number | null;
  status: string;
  push_status: string;
  created_at: string;
}

interface ChurchEvent {
  event_id: number;
  title: string;
  start_date: string;
}

interface PushDiff {
  event_id: number;
  event_title: string;
  will_attend: Array<{ member_id: number; name: string; included: boolean }>;
  missing: Array<{ member_id: number; name: string; included: boolean }>;
  duplicates_warn: string[];
}

interface DeadLetterRecord {
  id: number;
  contact_id: number;
  event_id: number;
  push_status: string;
  push_attempts: number;
  last_push_error: string | null;
  created_at: string;
}

export function AttendancePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selectedEventId, setSelectedEventId] = useState<number | null>(null);
  const [previewData, setPreviewData] = useState<PushDiff | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [activeTab, setActiveTab] = useState<'push' | 'deadletter'>('push');
  const [confirmPush, setConfirmPush] = useState(false);

  const { data: events = [] } = useQuery<ChurchEvent[]>({
    queryKey: ['events-list'],
    queryFn: () => api.get('/events').then((r) => r.data),
  });

  const { data: deadLetters = [] } = useQuery<DeadLetterRecord[]>({
    queryKey: ['dead-letter'],
    queryFn: () => api.get('/attendance/dead-letter').then((r) => r.data.items),
  });

  const runPreview = async () => {
    if (!selectedEventId) return;
    setPreviewing(true);
    setPreviewData(null);
    try {
      const res = await api.post(`/attendance/push-preview?event_id=${selectedEventId}`);
      setPreviewData(res.data);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Preview failed');
    } finally {
      setPreviewing(false);
    }
  };

  const pushAttendance = async () => {
    if (!selectedEventId) return;
    setConfirmPush(true);
  };

  const executePush = async () => {
    if (!selectedEventId) return;
    setConfirmPush(false);
    setPushing(true);
    try {
      const res = await api.post(`/attendance/push?event_id=${selectedEventId}`);
      toast.success(`Queued ${res.data.pushed} records for push`);
      setPreviewData(null);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Push failed');
    } finally {
      setPushing(false);
    }
  };

  const retryDeadLetter = async (id: number) => {
    try {
      await api.post(`/attendance/dead-letter/${id}/retry`);
      toast.success('Record queued for retry');
      queryClient.invalidateQueries({ queryKey: ['dead-letter'] });
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Retry failed');
    }
  };

  return (
    <div className="flex h-screen flex-col pb-20">
      {confirmPush && previewData && (
        <ConfirmDialog
          message={`Push ${previewData.will_attend.length} attendance record${previewData.will_attend.length !== 1 ? 's' : ''} for "${previewData.event_title}" to CiviCRM?`}
          confirmLabel="Push to CiviCRM"
          onConfirm={executePush}
          onCancel={() => setConfirmPush(false)}
        />
      )}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/settings')} aria-label="Back" className="text-foreground/50 hover:text-foreground">
            <ArrowLeft size={20} aria-hidden="true" />
          </button>
          <h1 className="text-lg font-bold text-foreground">Attendance & CiviCRM Push</h1>
        </div>
      </header>

      {/* Tabs */}
      <div className="flex border-b border-border bg-card">
        <button
          onClick={() => setActiveTab('push')}
          className={`flex-1 py-3 text-sm font-semibold transition-colors ${activeTab === 'push' ? 'border-b-2 border-primary text-primary' : 'text-foreground/50 hover:text-foreground'}`}
        >
          Push to CiviCRM
        </button>
        <button
          onClick={() => setActiveTab('deadletter')}
          className={`flex-1 py-3 text-sm font-semibold transition-colors ${activeTab === 'deadletter' ? 'border-b-2 border-primary text-primary' : 'text-foreground/50 hover:text-foreground'}`}
        >
          Dead Letter {deadLetters.length > 0 && <span className="ml-1 rounded-full bg-red-500 px-1.5 py-0.5 text-[10px] text-white">{deadLetters.length}</span>}
        </button>
      </div>

      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {activeTab === 'push' && (
          <div className="space-y-4">
            {/* Event selector */}
            <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
              <label htmlFor="event-select" className="mb-2 block text-xs font-semibold text-foreground/60">Select Event</label>
              <select
                id="event-select"
                value={selectedEventId ?? ''}
                onChange={(e) => { setSelectedEventId(Number(e.target.value) || null); setPreviewData(null); }}
                className="h-11 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30"
              >
                <option value="">Choose an event…</option>
                {events.map((ev) => (
                  <option key={ev.event_id} value={ev.event_id}>
                    {ev.title} — {new Date(ev.start_date).toLocaleDateString()}
                  </option>
                ))}
              </select>

              <button
                onClick={runPreview}
                disabled={!selectedEventId || previewing}
                className="mt-3 flex h-10 w-full items-center justify-center gap-2 rounded-xl border border-border bg-background text-sm font-semibold text-foreground transition-all hover:bg-primary/10 disabled:opacity-50"
              >
                <Eye size={15} aria-hidden="true" />
                {previewing ? 'Loading preview…' : 'Preview Push'}
              </button>
            </div>

            {/* Preview results */}
            {previewData && (
              <div className="rounded-2xl border border-border bg-card p-4 shadow-sm space-y-3">
                <h2 className="text-sm font-bold text-foreground">{previewData.event_title}</h2>

                {previewData.duplicates_warn.length > 0 && (
                  <div className="flex items-start gap-2 rounded-xl bg-amber-50 p-3 text-xs text-amber-700">
                    <AlertCircle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
                    <p>Duplicate detections: {previewData.duplicates_warn.join(', ')}</p>
                  </div>
                )}

                <div>
                  <p className="text-xs font-semibold text-foreground/60 mb-2">Will push ({previewData.will_attend.length})</p>
                  {previewData.will_attend.length === 0 ? (
                    <p className="text-xs text-foreground/40">No records to push</p>
                  ) : (
                    <ul className="space-y-1">
                      {previewData.will_attend.slice(0, 10).map((a) => (
                        <li key={a.member_id} className="flex items-center gap-2 text-xs text-foreground">
                          <span className="h-1.5 w-1.5 rounded-full bg-green-500" aria-hidden="true" />
                          {a.name}
                        </li>
                      ))}
                      {previewData.will_attend.length > 10 && (
                        <li className="text-xs text-foreground/40">…and {previewData.will_attend.length - 10} more</li>
                      )}
                    </ul>
                  )}
                </div>

                <button
                  onClick={pushAttendance}
                  disabled={pushing || previewData.will_attend.length === 0}
                  className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-primary font-bold text-primary-foreground shadow-sm transition-all hover:bg-primary/85 disabled:opacity-50"
                >
                  <Send size={15} aria-hidden="true" />
                  {pushing ? 'Pushing…' : `Push ${previewData.will_attend.length} Records to CiviCRM`}
                </button>
              </div>
            )}
          </div>
        )}

        {activeTab === 'deadletter' && (
          <div className="space-y-2 pb-4">
            {deadLetters.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-foreground/50">
                <p className="text-sm font-medium">No dead-letter records</p>
                <p className="mt-1 text-xs">Failed pushes that exceeded retry limit appear here</p>
              </div>
            ) : (
              deadLetters.map((dl) => (
                <div key={dl.id} className="rounded-2xl border border-red-200 bg-card p-4 shadow-sm">
                  <div className="flex items-start justify-between">
                    <div>
                      <p className="text-sm font-semibold text-foreground">Contact #{dl.contact_id} / Event #{dl.event_id}</p>
                      <p className="text-xs text-foreground/50">{dl.push_attempts} attempts · {new Date(dl.created_at).toLocaleString()}</p>
                      {dl.last_push_error && (
                        <p className="mt-1 text-xs text-red-500">{dl.last_push_error}</p>
                      )}
                    </div>
                    <button
                      onClick={() => retryDeadLetter(dl.id)}
                      aria-label="Retry this record"
                      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-border bg-background text-foreground/50 hover:text-primary hover:bg-primary/10"
                    >
                      <RotateCcw size={14} aria-hidden="true" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </main>
    </div>
  );
}
