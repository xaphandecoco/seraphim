// S08 — RetentionReport — admin paginated view of biometric retention data
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, ShieldCheck } from 'lucide-react';
import { biometricApi } from '@/services/biometric';
import { LoadingState, EmptyState, ErrorState } from '@/components/ui/StateViews';
import type { RetentionReportParams, RetentionReportItem } from '@/types/biometric';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;

const WITHIN_DAYS_OPTIONS: Array<{ value: number | null; label: string }> = [
  { value: null, label: 'All time' },
  { value: 30, label: 'Next 30 days' },
  { value: 60, label: 'Next 60 days' },
  { value: 90, label: 'Next 90 days' },
  { value: 365, label: 'Next year' },
];

// ---------------------------------------------------------------------------
// Status pill (local, reuse same colour map as ConsentPanel)
// ---------------------------------------------------------------------------

const STATUS_COLOURS: Record<string, string> = {
  none: 'bg-muted text-foreground/60',
  pending: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  given: 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300',
  revoked: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
  purged: 'bg-foreground/10 text-foreground/50',
};

function StatusPill({ status }: { status: string }) {
  const cls = STATUS_COLOURS[status] ?? 'bg-muted text-foreground/60';
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold capitalize ${cls}`}
    >
      {status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function ReportRow({ item }: { item: RetentionReportItem }) {
  const navigate = useNavigate();

  return (
    <div className="rounded-2xl border border-border bg-card p-3 shadow-sm space-y-1">
      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          onClick={() => navigate(`/contacts/${item.contact_id}`)}
          className="text-sm font-semibold text-primary hover:underline focus:outline-none focus:ring-2 focus:ring-ring rounded text-left"
        >
          {item.contact_name}
        </button>
        <StatusPill status={item.status} />
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-foreground/50">
        <span>
          Consent:{' '}
          <span
            className={
              item.consent_given ? 'text-green-600 dark:text-green-400' : 'text-foreground/60'
            }
          >
            {item.consent_given ? 'Yes' : 'No'}
          </span>
        </span>
        {item.retention_until && (
          <span>
            Retain until:{' '}
            <span className="text-foreground">
              {new Date(item.retention_until).toLocaleDateString()}
            </span>
          </span>
        )}
        {item.deletion_requested_at && (
          <span>
            Deletion req.:{' '}
            <span className="text-red-600 dark:text-red-400">
              {new Date(item.deletion_requested_at).toLocaleDateString()}
            </span>
          </span>
        )}
        {item.purged_at && (
          <span>
            Purged:{' '}
            <span className="text-foreground">
              {new Date(item.purged_at).toLocaleDateString()}
            </span>
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function RetentionReport() {
  const navigate = useNavigate();

  // Filters
  const [withinDays, setWithinDays] = useState<number | null>(null);
  const [includePurged, setIncludePurged] = useState(false);

  // Pagination
  const [page, setPage] = useState(1);

  const params: RetentionReportParams = {
    page,
    page_size: PAGE_SIZE,
    ...(withinDays !== null ? { within_days: withinDays } : {}),
    include_purged: includePurged,
  };

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['biometric', 'retention', params],
    queryFn: () => biometricApi.getRetentionReport(params),
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  // Reset page when filters change
  function applyWithinDays(value: number | null) {
    setWithinDays(value);
    setPage(1);
  }

  function applyIncludePurged(value: boolean) {
    setIncludePurged(value);
    setPage(1);
  }

  return (
    <div className="flex h-screen flex-col pb-20">
      {/* Header */}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => navigate('/settings')}
            aria-label="Back to Settings"
            className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <ArrowLeft size={18} aria-hidden="true" />
          </button>
          <div className="flex items-center gap-2">
            <ShieldCheck size={18} className="text-primary" aria-hidden="true" />
            <h1 className="text-lg font-bold text-foreground">Biometric Retention</h1>
          </div>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-3 pt-3 space-y-3">
        {/* Filters */}
        <div className="rounded-2xl border border-border bg-card p-3 flex flex-wrap items-center gap-3">
          {/* Within days select */}
          <div className="flex items-center gap-2">
            <label
              htmlFor="within-days-select"
              className="text-xs font-medium text-foreground/60 shrink-0"
            >
              Retention window:
            </label>
            <select
              id="within-days-select"
              value={withinDays ?? ''}
              onChange={(e) =>
                applyWithinDays(e.target.value === '' ? null : Number(e.target.value))
              }
              className="rounded-xl border border-border bg-background px-2 py-1.5 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {WITHIN_DAYS_OPTIONS.map((opt) => (
                <option key={opt.value ?? ''} value={opt.value ?? ''}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          {/* Include purged checkbox */}
          <label className="flex items-center gap-2 text-xs font-medium text-foreground/60 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={includePurged}
              onChange={(e) => applyIncludePurged(e.target.checked)}
              className="h-4 w-4 rounded border-border text-primary focus:ring-ring"
            />
            Include purged
          </label>

          {total > 0 && (
            <span className="ml-auto text-xs text-foreground/40">
              {total} record{total !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        {/* Body */}
        {isLoading ? (
          <LoadingState message="Loading retention report…" />
        ) : isError ? (
          <ErrorState
            message="Failed to load retention report"
            onRetry={() => refetch()}
          />
        ) : items.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="No records found"
            description="No contacts match the current filters."
          />
        ) : (
          <>
            <div className="space-y-2 pb-2">
              {items.map((item) => (
                <ReportRow key={item.contact_id} item={item} />
              ))}
            </div>

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-center gap-2 py-4">
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  className="rounded-xl bg-background px-3 py-1.5 text-sm font-medium text-foreground border border-border transition-all hover:bg-primary/20 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  Prev
                </button>
                <span className="text-sm text-foreground/50">
                  Page {page} of {totalPages}
                </span>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                  className="rounded-xl bg-background px-3 py-1.5 text-sm font-medium text-foreground border border-border transition-all hover:bg-primary/20 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
