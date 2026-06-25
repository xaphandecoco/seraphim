import { useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { AlertTriangle, ChevronDown, ChevronUp, RefreshCw, Trash2, Upload, UserX } from 'lucide-react';
import { api } from '@/services/api';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { LoadingState, EmptyState, ErrorState } from '@/components/ui/StateViews';
import type { FacePanelData, FaceSample } from '@/types';

interface FacePanelProps {
  contactId: number;
  readOnly?: boolean;
}

interface RecognitionEvent {
  id: number;
  detected_at: string;
  confidence: number | null;
  camera_name: string | null;
  event_title: string | null;
}

async function fetchFacePanel(contactId: number): Promise<FacePanelData> {
  const res = await api.get<FacePanelData>(`/contacts/${contactId}/faces`);
  return res.data;
}

async function fetchRecognitionHistory(contactId: number): Promise<RecognitionEvent[]> {
  const res = await api.get<RecognitionEvent[]>(`/contacts/${contactId}/recognition-history`);
  return res.data;
}

function enrollmentStatusClass(status: string | null): string {
  if (status === 'active') return 'bg-green-100 text-green-800';
  if (status === 'pending') return 'bg-amber-100 text-amber-800';
  return 'bg-background text-foreground/50';
}

function enrollmentStatusLabel(status: string | null): string {
  if (status === 'active') return 'Active';
  if (status === 'pending') return 'Pending';
  return 'Not Enrolled';
}

export function FacePanel({ contactId, readOnly = false }: FacePanelProps) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [removingId, setRemovingId] = useState<number | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  const {
    data: panel,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['contacts', contactId, 'faces'],
    queryFn: () => fetchFacePanel(contactId),
  });

  const {
    data: history = [],
    isLoading: historyLoading,
    isError: historyError,
  } = useQuery({
    queryKey: ['contacts', contactId, 'recognition-history'],
    queryFn: () => fetchRecognitionHistory(contactId),
    enabled: historyOpen,
  });

  const deleteMutation = useMutation({
    mutationFn: async (sampleId: number) => {
      await api.delete(`/contacts/${contactId}/faces/${sampleId}`);
    },
    onSuccess: () => {
      toast.success('Sample removed');
      queryClient.invalidateQueries({ queryKey: ['contacts', contactId, 'faces'] });
      setRemovingId(null);
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || 'Failed to remove sample');
      setRemovingId(null);
    },
  });

  const retrainMutation = useMutation({
    mutationFn: async () => {
      await api.post(`/contacts/${contactId}/faces/retrain`);
    },
    onSuccess: () => {
      toast.success('Retrain queued');
      queryClient.invalidateQueries({ queryKey: ['contacts', contactId, 'faces'] });
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || 'Retrain failed');
    },
  });

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append('file', file);
      // axios sets Content-Type with the correct multipart boundary automatically for FormData
      await api.post(`/contacts/${contactId}/faces`, form);
    },
    onSuccess: () => {
      toast.success('Photo uploaded');
      queryClient.invalidateQueries({ queryKey: ['contacts', contactId, 'faces'] });
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || 'Upload failed');
    },
  });

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      uploadMutation.mutate(file);
    }
    // Reset so the same file can be re-selected if needed
    e.target.value = '';
  };

  if (isLoading) return <LoadingState message="Loading face data…" />;
  if (isError) return <ErrorState message="Failed to load face data" onRetry={() => refetch()} />;

  const isPurged = panel?.purged_at != null;
  const isOrphan = panel?.is_orphan === true;
  const samples: FaceSample[] = panel?.samples ?? [];
  const enrollmentStatus = panel?.enrollment_status ?? null;
  const showMutations = !readOnly && !isPurged;

  return (
    <div className="flex min-w-[375px] flex-col gap-4 p-4">
      {/* Purged warning banner */}
      {isPurged && (
        <div
          className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
          role="alert"
        >
          <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            Face data was purged on{' '}
            {new Date(panel!.purged_at!).toLocaleDateString()}. No new samples or
            retraining is allowed.
          </span>
        </div>
      )}

      {/* Orphan warning banner */}
      {isOrphan && (
        <div
          className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300"
          role="alert"
        >
          <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            This contact has enrolled samples but no active CompreFace subject. The
            recognition data may be out of sync.
          </span>
        </div>
      )}

      {/* Header row: status pill + action buttons */}
      <div className="flex items-center justify-between gap-2">
        <span
          className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${enrollmentStatusClass(enrollmentStatus)}`}
        >
          {enrollmentStatusLabel(enrollmentStatus)}
        </span>

        {showMutations && (
          <div className="flex items-center gap-2">
            {/* Add photo */}
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadMutation.isPending}
              aria-label="Add face photo"
              className="flex min-h-[44px] items-center gap-1.5 rounded-xl border border-border bg-background px-3 text-sm font-semibold text-foreground transition-colors hover:bg-primary/10 disabled:opacity-50"
            >
              <Upload size={14} aria-hidden="true" />
              Add
            </button>
            {/* Hidden file input */}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              // eslint-disable-next-line @typescript-eslint/ban-ts-comment
              // @ts-ignore — non-standard attribute for mobile camera
              capture="environment"
              className="hidden"
              onChange={handleFileChange}
              aria-hidden="true"
            />

            {/* Retrain */}
            <button
              onClick={() => retrainMutation.mutate()}
              disabled={retrainMutation.isPending || samples.length === 0}
              aria-label="Retrain face model"
              className="flex min-h-[44px] items-center gap-1.5 rounded-xl border border-border bg-background px-3 text-sm font-semibold text-foreground transition-colors hover:bg-primary/10 disabled:opacity-50"
            >
              <RefreshCw
                size={14}
                className={retrainMutation.isPending ? 'animate-spin' : ''}
                aria-hidden="true"
              />
              {retrainMutation.isPending ? 'Queuing…' : 'Retrain'}
            </button>
          </div>
        )}
      </div>

      {/* Sample grid */}
      {samples.length === 0 ? (
        <EmptyState
          icon={UserX}
          title="No enrolled samples"
          description={
            readOnly
              ? 'No face samples have been enrolled for this contact.'
              : 'Add photos to enroll this contact.'
          }
        />
      ) : (
        <div className="grid grid-cols-2 gap-3">
          {samples.map((sample) => (
            <SampleTile
              key={sample.id}
              sample={sample}
              showRemove={showMutations}
              onRemove={() => setRemovingId(sample.id)}
            />
          ))}
        </div>
      )}

      {/* Recognition history accordion */}
      <div className="rounded-xl border border-border bg-card">
        <button
          onClick={() => setHistoryOpen((o) => !o)}
          className="flex min-h-[44px] w-full items-center justify-between px-4 text-sm font-semibold text-foreground"
          aria-expanded={historyOpen}
          aria-controls="recognition-history-panel"
        >
          <span>Recognition History</span>
          {historyOpen ? (
            <ChevronUp size={16} aria-hidden="true" />
          ) : (
            <ChevronDown size={16} aria-hidden="true" />
          )}
        </button>

        {historyOpen && (
          <div id="recognition-history-panel" className="border-t border-border px-4 pb-4 pt-3">
            {historyLoading && <LoadingState message="Loading history…" />}
            {historyError && (
              <ErrorState message="Failed to load recognition history" />
            )}
            {!historyLoading && !historyError && history.length === 0 && (
              <p className="text-center text-sm text-foreground/50">No recognition events yet.</p>
            )}
            {!historyLoading && !historyError && history.length > 0 && (
              <ul className="flex flex-col gap-2" role="list">
                {history.map((event) => (
                  <li
                    key={event.id}
                    className="flex items-center justify-between rounded-lg bg-background px-3 py-2 text-sm"
                  >
                    <div className="flex flex-col">
                      <span className="font-medium text-foreground">
                        {new Date(event.detected_at).toLocaleString()}
                      </span>
                      {event.camera_name && (
                        <span className="text-xs text-foreground/50">{event.camera_name}</span>
                      )}
                      {event.event_title && (
                        <span className="text-xs text-foreground/50">{event.event_title}</span>
                      )}
                    </div>
                    {event.confidence != null && (
                      <span className="ml-2 shrink-0 text-xs font-semibold text-foreground/70">
                        {Math.round(event.confidence * 100)}%
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {/* Remove confirm dialog */}
      {removingId != null && (
        <ConfirmDialog
          message="Remove this face sample? This cannot be undone."
          confirmLabel="Remove"
          destructive
          onConfirm={() => deleteMutation.mutate(removingId)}
          onCancel={() => setRemovingId(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-component: single sample tile
// ---------------------------------------------------------------------------

interface SampleTileProps {
  sample: FaceSample;
  showRemove: boolean;
  onRemove: () => void;
}

function SampleTile({ sample, showRemove, onRemove }: SampleTileProps) {
  const thumbUrl = sample.thumb_path ? `/api${sample.thumb_path}` : null;

  return (
    <div className="relative overflow-hidden rounded-xl border border-border bg-card">
      {thumbUrl ? (
        <img
          src={thumbUrl}
          alt={`Face sample ${sample.id}`}
          className="h-20 w-full object-cover"
          loading="lazy"
        />
      ) : (
        <div className="flex h-20 w-full items-center justify-center bg-background">
          <span className="text-xs text-foreground/40">No preview</span>
        </div>
      )}

      {showRemove && (
        <button
          onClick={onRemove}
          aria-label="Remove sample"
          className="absolute right-1 top-1 flex min-h-[44px] min-w-[44px] items-center justify-center rounded-xl bg-red-500/90 text-white transition-colors hover:bg-red-600 focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <Trash2 size={14} aria-hidden="true" />
        </button>
      )}

      {sample.created_at && (
        <p className="px-2 py-1 text-center text-xs text-foreground/50">
          {new Date(sample.created_at).toLocaleDateString()}
        </p>
      )}
    </div>
  );
}
