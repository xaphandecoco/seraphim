import { useCallback, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft, Upload, X, ImageIcon } from 'lucide-react';
import { api } from '@/services/api';
import { LoadingState, EmptyState, ErrorState } from '@/components/ui/StateViews';
import type { ChurchEvent } from '@/types';

// ---------------------------------------------------------------------------
// Local types (PhotoIngestBatch not yet in global types/index.ts)
// ---------------------------------------------------------------------------

// Shape matches backend photo_ingest.py report entries:
//   { filename, faces_detected, outcome, error? }
// tasks_created / auto_logged / quality_failed are batch-level aggregates only.
interface ImageResult {
  filename: string;
  faces_detected: number;
  outcome: string;   // "ok" | "decode_error" | "oversize" | ...
  error?: string;
}

interface PhotoIngestBatch {
  id: number;
  status: 'processing' | 'completed' | 'failed';
  total_images: number;
  processed_images: number;
  report: ImageResult[];
}

// ---------------------------------------------------------------------------
// File preview item
// ---------------------------------------------------------------------------

interface PreviewFile {
  file: File;
  thumbUrl: string;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const CHUNK_SIZE = 50;

// ---------------------------------------------------------------------------
// BulkPhotoUploadPage
// ---------------------------------------------------------------------------

export function BulkPhotoUploadPage() {
  const navigate = useNavigate();

  const [previews, setPreviews] = useState<PreviewFile[]>([]);
  const [selectedEventId, setSelectedEventId] = useState<number | ''>('');
  const [batchId, setBatchId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Track total files submitted across all chunks for progress calculation
  const totalSubmittedRef = useRef<number>(0);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // ------------------------------------------------------------------
  // Events dropdown (same pattern as EventsPage.tsx)
  // ------------------------------------------------------------------
  const {
    data: events = [],
    isLoading: eventsLoading,
    isError: eventsError,
    refetch: refetchEvents,
  } = useQuery<ChurchEvent[]>({
    queryKey: ['events'],
    queryFn: () => api.get('/events').then((r) => r.data),
  });

  // ------------------------------------------------------------------
  // Batch status polling (acceptance criteria 5 & 6)
  // refetchInterval: 2000 when processing, false otherwise
  // ------------------------------------------------------------------
  const {
    data: batch,
    isLoading: batchLoading,
    isError: batchError,
  } = useQuery<PhotoIngestBatch>({
    queryKey: ['photo-ingest-batch', batchId],
    queryFn: () => api.get(`/uploads/photos/batch/${batchId}`).then((r) => r.data),
    enabled: batchId !== null,
    refetchInterval: (query) => {
      const data = query.state.data;
      return data?.status === 'processing' ? 2000 : false;
    },
  });

  // ------------------------------------------------------------------
  // File selection helpers
  // ------------------------------------------------------------------
  const addFiles = useCallback((incoming: FileList | File[]) => {
    const imageFiles = Array.from(incoming).filter((f) => f.type.startsWith('image/'));
    if (imageFiles.length === 0) return;

    setPreviews((prev) => {
      const combined = [...prev];
      for (const f of imageFiles) {
        if (combined.length >= 50) break;
        // Avoid duplicates by name+size (best-effort)
        if (combined.some((p) => p.file.name === f.name && p.file.size === f.size)) continue;
        combined.push({ file: f, thumbUrl: URL.createObjectURL(f) });
      }
      if (combined.length === 50 && prev.length < 50) {
        toast.info('Maximum of 50 files selected');
      }
      return combined;
    });
  }, []);

  const removeFile = useCallback((index: number) => {
    setPreviews((prev) => {
      const next = [...prev];
      URL.revokeObjectURL(next[index].thumbUrl);
      next.splice(index, 1);
      return next;
    });
  }, []);

  const clearAll = useCallback(() => {
    setPreviews((prev) => {
      prev.forEach((p) => URL.revokeObjectURL(p.thumbUrl));
      return [];
    });
    setBatchId(null);
    totalSubmittedRef.current = 0;
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, []);

  // ------------------------------------------------------------------
  // Drag-and-drop
  // ------------------------------------------------------------------
  const onDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer.files) addFiles(e.dataTransfer.files);
  };

  // ------------------------------------------------------------------
  // Submit — chunk files into 50-item batches (acceptance criterion 4)
  // ------------------------------------------------------------------
  const handleSubmit = async () => {
    if (previews.length === 0) {
      toast.error('Select at least one image before uploading');
      return;
    }
    setSubmitting(true);
    totalSubmittedRef.current = previews.length;

    const files = previews.map((p) => p.file);
    const chunks: File[][] = [];
    for (let i = 0; i < files.length; i += CHUNK_SIZE) {
      chunks.push(files.slice(i, i + CHUNK_SIZE));
    }

    let lastBatchId: string | null = null;
    try {
      for (const chunk of chunks) {
        const formData = new FormData();
        for (const f of chunk) {
          formData.append('files', f);
        }
        // Backend reads event_id as a Query param, not a form field
        const url =
          '/uploads/photos/batch' +
          (selectedEventId !== '' ? `?event_id=${selectedEventId}` : '');

        const res = await api.post<PhotoIngestBatch>(url, formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        // Backend returns id (int), not batch_id
        lastBatchId = String(res.data.id);
      }
      setBatchId(lastBatchId);
      toast.success('Upload submitted — processing…');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Upload failed');
    } finally {
      setSubmitting(false);
    }
  };

  // ------------------------------------------------------------------
  // Progress (acceptance criterion 6)
  // ------------------------------------------------------------------
  const totalSubmitted = totalSubmittedRef.current || (batch?.total_images ?? 0);
  const progressPct =
    batch && totalSubmitted > 0
      ? Math.min(100, Math.round((batch.processed_images / totalSubmitted) * 100))
      : 0;

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  return (
    <div className="flex h-screen flex-col">
      {/* Header */}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/dashboard')}
            aria-label="Back to dashboard"
            className="text-foreground/50 hover:text-foreground"
          >
            <ArrowLeft size={20} aria-hidden="true" />
          </button>
          <h1 className="text-lg font-bold text-foreground">Bulk Photo Upload</h1>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-4 py-4 space-y-5">
        {/* ---- Event selector ---- */}
        <section aria-labelledby="event-label">
          <label
            id="event-label"
            htmlFor="event-select"
            className="mb-1 block text-xs font-semibold uppercase tracking-wide text-foreground/50"
          >
            Link to event (optional)
          </label>
          {eventsLoading ? (
            <LoadingState message="Loading events…" />
          ) : eventsError ? (
            <ErrorState message="Could not load events" onRetry={refetchEvents} />
          ) : events.length === 0 ? (
            <EmptyState title="No events available" />
          ) : (
            <select
              id="event-select"
              value={selectedEventId}
              onChange={(e) =>
                setSelectedEventId(e.target.value === '' ? '' : Number(e.target.value))
              }
              disabled={submitting || batchId !== null}
              className="min-h-[44px] w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
            >
              <option value="">— No event —</option>
              {events.map((ev) => (
                <option key={ev.id} value={ev.id}>
                  {ev.title}
                  {ev.start_at
                    ? ` (${new Date(ev.start_at).toLocaleDateString(undefined, {
                        month: 'short',
                        day: 'numeric',
                        year: 'numeric',
                      })})`
                    : ''}
                </option>
              ))}
            </select>
          )}
        </section>

        {/* ---- Drop zone ---- */}
        {batchId === null && (
          <section aria-labelledby="dropzone-label">
            <p
              id="dropzone-label"
              className="mb-1 block text-xs font-semibold uppercase tracking-wide text-foreground/50"
            >
              Select images ({previews.length}/50)
            </p>
            <div
              role="button"
              tabIndex={0}
              aria-label="Drop images here or tap to browse"
              onDragOver={onDragOver}
              onDrop={onDrop}
              onClick={() => fileInputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click();
              }}
              className="flex min-h-[140px] cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-border bg-background transition-colors hover:border-primary/40 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
            >
              <Upload size={28} className="text-foreground/30" aria-hidden="true" />
              <p className="text-sm font-medium text-foreground/50">
                Drop images here or tap to browse
              </p>
              <p className="text-xs text-foreground/30">image/*, up to 50 files</p>
            </div>
            {/* Hidden file input — capture='environment' for mobile camera roll */}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              capture="environment"
              className="sr-only"
              aria-hidden="true"
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files);
                e.target.value = '';
              }}
            />
          </section>
        )}

        {/* ---- Preview list (acceptance criterion 2) ---- */}
        {previews.length > 0 && batchId === null && (
          <section aria-labelledby="preview-label">
            <div className="mb-2 flex items-center justify-between">
              <p
                id="preview-label"
                className="text-xs font-semibold uppercase tracking-wide text-foreground/50"
              >
                Selected files
              </p>
              <button
                onClick={clearAll}
                className="text-xs text-foreground/40 underline hover:text-foreground"
              >
                Clear all
              </button>
            </div>
            <ul className="space-y-2" aria-label="Selected image files">
              {previews.map((p, i) => (
                <li
                  key={`${p.file.name}-${p.file.size}-${i}`}
                  className="flex items-center gap-3 rounded-xl border border-border bg-card px-3 py-2"
                >
                  {/* 40x40 thumbnail */}
                  <img
                    src={p.thumbUrl}
                    alt={p.file.name}
                    width={40}
                    height={40}
                    className="h-10 w-10 shrink-0 rounded-lg object-cover"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-foreground">{p.file.name}</p>
                    <p className="text-xs text-foreground/40">{formatBytes(p.file.size)}</p>
                  </div>
                  <button
                    onClick={() => removeFile(i)}
                    aria-label={`Remove ${p.file.name}`}
                    className="shrink-0 text-foreground/30 hover:text-red-500"
                  >
                    <X size={16} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* ---- Submit button ---- */}
        {previews.length > 0 && batchId === null && (
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="min-h-[44px] w-full rounded-xl bg-primary px-4 py-3 text-sm font-bold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {submitting
              ? 'Uploading…'
              : `Upload ${previews.length} image${previews.length !== 1 ? 's' : ''}`}
          </button>
        )}

        {/* ---- Processing / progress (acceptance criteria 5 & 6) ---- */}
        {batchId !== null && (
          <section aria-labelledby="status-label" className="space-y-4">
            <h2
              id="status-label"
              className="text-xs font-semibold uppercase tracking-wide text-foreground/50"
            >
              Batch status
            </h2>

            {batchLoading && !batch && <LoadingState message="Fetching batch status…" />}
            {batchError && (
              <ErrorState message="Could not load batch status" />
            )}

            {batch && (
              <>
                {/* Progress bar */}
                {(batch.status === 'processing' || progressPct > 0) && (
                  <div aria-label={`Processing progress: ${progressPct}%`}>
                    <div className="mb-1 flex justify-between text-xs text-foreground/50">
                      <span>
                        {batch.processed_images} / {totalSubmitted} images
                      </span>
                      <span>{progressPct}%</span>
                    </div>
                    <div className="h-2 w-full overflow-hidden rounded-full bg-border">
                      <div
                        role="progressbar"
                        aria-valuenow={progressPct}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        className="h-full rounded-full bg-primary transition-all duration-500"
                        style={{ width: `${progressPct}%` }}
                      />
                    </div>
                    {batch.status === 'processing' && (
                      <p className="mt-1 text-xs text-foreground/40" aria-live="polite">
                        Processing…
                      </p>
                    )}
                  </div>
                )}

                {/* Status badge */}
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-bold ${
                      batch.status === 'completed'
                        ? 'bg-green-100 text-green-700'
                        : batch.status === 'failed'
                        ? 'bg-red-100 text-red-600'
                        : 'bg-amber-100 text-amber-700'
                    }`}
                  >
                    {batch.status.charAt(0).toUpperCase() + batch.status.slice(1)}
                  </span>
                  <span className="text-xs text-foreground/50">
                    Batch {batchId.slice(0, 8)}…
                  </span>
                </div>

                {/* Per-image results table (acceptance criterion 7) */}
                {batch.status === 'completed' && batch.report.length > 0 && (
                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-foreground/50">
                      Results
                    </h3>
                    <div className="overflow-x-auto rounded-xl border border-border">
                      <table className="w-full min-w-[400px] text-xs" aria-label="Per-image results">
                        <thead className="bg-background">
                          <tr>
                            <th className="px-3 py-2 text-left font-semibold text-foreground/60">
                              File
                            </th>
                            <th className="px-3 py-2 text-right font-semibold text-foreground/60">
                              Faces
                            </th>
                            <th className="px-3 py-2 text-left font-semibold text-foreground/60">
                              Outcome
                            </th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-border">
                          {batch.report.map((row, i) => (
                            <tr key={i} className="bg-card">
                              <td className="max-w-[140px] truncate px-3 py-2 font-medium text-foreground">
                                {row.error ? (
                                  <span className="flex items-center gap-1 text-red-500">
                                    <ImageIcon size={12} aria-hidden="true" />
                                    <span className="truncate">{row.filename}</span>
                                  </span>
                                ) : (
                                  row.filename
                                )}
                              </td>
                              <td className="px-3 py-2 text-right text-foreground">
                                {row.faces_detected}
                              </td>
                              <td className="px-3 py-2 text-left text-foreground">
                                <span
                                  className={
                                    row.outcome === 'ok'
                                      ? 'text-green-600'
                                      : 'text-red-500'
                                  }
                                >
                                  {row.outcome}
                                </span>
                                {row.error && (
                                  <span className="ml-1 text-foreground/40">({row.error})</span>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Empty results */}
                {batch.status === 'completed' && batch.report.length === 0 && (
                  <EmptyState
                    icon={ImageIcon}
                    title="No results returned"
                    description="The batch completed but reported no per-image data."
                  />
                )}

                {/* Upload another batch */}
                {(batch.status === 'completed' || batch.status === 'failed') && (
                  <button
                    onClick={clearAll}
                    className="min-h-[44px] w-full rounded-xl border border-border bg-background px-4 py-3 text-sm font-semibold text-foreground transition-colors hover:bg-primary/10"
                  >
                    Upload another batch
                  </button>
                )}
              </>
            )}
          </section>
        )}

        {/* ---- Idle empty state ---- */}
        {previews.length === 0 && batchId === null && (
          <EmptyState
            icon={Upload}
            title="No files selected"
            description="Use the drop zone above to add up to 50 images."
          />
        )}
      </main>
    </div>
  );
}
