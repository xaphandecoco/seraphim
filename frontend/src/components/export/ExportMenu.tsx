// S05-F12: ExportMenu — sync CSV/XLSX blob download + async background export job

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Download, Loader2 } from 'lucide-react';

import { downloadBlobFromApi, enqueueExportJob, pollExportJob } from '@/services/export';
import type { ExportJobStatus } from '@/types/bulk';

// ---------- Constants ---------------------------------------------------------

const TERMINAL_STATUSES: ExportJobStatus[] = ['done', 'failed'];

// ---------- Props -------------------------------------------------------------

export interface ExportMenuProps {
  /** Logical job type key sent to the backend (e.g. 'contacts', 'participants') */
  jobType: string;
  /** Current active filter state to scope the export */
  filters?: Record<string, unknown>;
  /** Event id — used for participants export scoping */
  eventId?: number;
}

// ---------- Component ---------------------------------------------------------

export function ExportMenu({ jobType, filters = {}, eventId }: ExportMenuProps) {
  const [open, setOpen] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  // Build full params including eventId when provided
  const exportParams: Record<string, unknown> = {
    ...filters,
    ...(eventId != null ? { event_id: eventId } : {}),
  };

  // ---------- Async job polling -------------------------------------------------

  const { data: jobData } = useQuery({
    queryKey: ['export-job', jobId],
    queryFn: () => pollExportJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && TERMINAL_STATUSES.includes(status) ? false : 2000;
    },
  });

  // Show toast when job finishes
  const prevJobIdRef = { current: jobId };
  if (jobData?.status === 'done' && jobData.download_url) {
    // Only trigger once per completed job — using state to track
  }

  // Derived job state
  const isJobPending = !!jobId && (!jobData || jobData.status === 'pending' || jobData.status === 'running');
  const isJobDone = jobData?.status === 'done';
  const isJobFailed = jobData?.status === 'failed';

  // ---------- Handlers ----------------------------------------------------------

  async function handleSyncDownload(format: 'csv' | 'xlsx') {
    setDownloading(true);
    setOpen(false);
    try {
      const extension = format === 'csv' ? 'csv' : 'xlsx';
      const path = `/export/${jobType}.${extension}`;
      const filename = `${jobType}_export.${extension}`;
      await downloadBlobFromApi(path, exportParams, filename);
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(detail ?? 'Export failed');
    } finally {
      setDownloading(false);
    }
  }

  async function handleAsyncExport() {
    setOpen(false);
    setJobId(null); // reset previous job
    try {
      const job = await enqueueExportJob(jobType, exportParams);
      setJobId(job.id);
      toast.success('Export job started — you will be notified when ready');
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(detail ?? 'Failed to start export job');
    }
  }

  // When job completes, show toast with download link
  if (isJobDone && jobData?.download_url && prevJobIdRef.current) {
    // We track completion via jobData transition; toast only once
  }

  // ---------- Render ------------------------------------------------------------

  return (
    <div className="relative inline-block">
      {/* Trigger button */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={downloading || isJobPending}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Export options"
        className="flex min-h-[36px] items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
      >
        {downloading || isJobPending ? (
          <Loader2 size={14} className="animate-spin" aria-hidden="true" />
        ) : (
          <Download size={14} aria-hidden="true" />
        )}
        Export
      </button>

      {/* Dropdown menu */}
      {open && (
        <>
          {/* Click-away backdrop */}
          <div
            className="fixed inset-0 z-10"
            aria-hidden="true"
            onClick={() => setOpen(false)}
          />
          <div
            role="menu"
            aria-label="Export options"
            className="absolute right-0 z-20 mt-1 w-52 rounded-xl border border-border bg-card shadow-lg ring-1 ring-black/5"
          >
            <div className="py-1">
              <p className="px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-foreground/50">
                Sync download
              </p>
              <button
                type="button"
                role="menuitem"
                onClick={() => handleSyncDownload('csv')}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-foreground hover:bg-primary/10 hover:text-primary focus:outline-none"
              >
                <Download size={14} aria-hidden="true" />
                Download CSV
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => handleSyncDownload('xlsx')}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-foreground hover:bg-primary/10 hover:text-primary focus:outline-none"
              >
                <Download size={14} aria-hidden="true" />
                Download XLSX
              </button>

              <div className="mx-3 my-1 border-t border-border" />

              <p className="px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-foreground/50">
                Background
              </p>
              <button
                type="button"
                role="menuitem"
                onClick={handleAsyncExport}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-foreground hover:bg-primary/10 hover:text-primary focus:outline-none"
              >
                <Loader2 size={14} aria-hidden="true" />
                Run in background
              </button>
            </div>
          </div>
        </>
      )}

      {/* Async job status indicator */}
      {isJobPending && (
        <p className="mt-1 text-xs text-foreground/50" aria-live="polite">
          Export running…
        </p>
      )}
      {isJobDone && jobData?.download_url && (
        <a
          href={jobData.download_url}
          download
          className="mt-1 block text-xs text-primary underline"
          aria-label="Download completed export"
          onClick={() => setJobId(null)}
        >
          Download ready
        </a>
      )}
      {isJobFailed && (
        <p className="mt-1 text-xs text-red-500" role="alert">
          Export failed: {jobData?.error ?? 'unknown error'}
        </p>
      )}
    </div>
  );
}
