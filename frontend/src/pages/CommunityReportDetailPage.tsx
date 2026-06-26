import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft, Loader2, RefreshCw } from 'lucide-react';
import { getCommunityReport, processCommunityReport } from '@/services/communityReports';
import type { CommunityReportStatus } from '@/types/nameMatch';

function StatusBadge({ status }: { status: CommunityReportStatus }) {
  const styles: Record<CommunityReportStatus, string> = {
    submitted: 'bg-blue-50 text-blue-700 border-blue-200',
    pending: 'bg-amber-50 text-amber-700 border-amber-200',
    processing: 'bg-blue-50 text-blue-600 border-blue-200',
    complete: 'bg-green-50 text-green-700 border-green-200',
    partial: 'bg-orange-50 text-orange-600 border-orange-200',
    archived: 'bg-gray-100 text-gray-500 border-gray-200',
  };
  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${styles[status] ?? 'bg-muted text-muted-foreground border-border'}`}>
      {status}
    </span>
  );
}

function MatchStatusBadge({ matchStatus }: { matchStatus: string }) {
  const styles: Record<string, string> = {
    pending: 'bg-amber-50 text-amber-700 border-amber-200',
    processing: 'bg-blue-50 text-blue-600 border-blue-200',
    complete: 'bg-green-50 text-green-700 border-green-200',
    partial: 'bg-orange-50 text-orange-600 border-orange-200',
  };
  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${styles[matchStatus] ?? 'bg-muted text-muted-foreground border-border'}`}>
      {matchStatus}
    </span>
  );
}

function ReviewStatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: 'bg-amber-50 text-amber-700',
    matched: 'bg-green-50 text-green-700',
    unmatched: 'bg-red-50 text-red-600',
    skipped: 'bg-gray-100 text-gray-500',
  };
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${styles[status] ?? 'bg-muted text-muted-foreground'}`}>
      {status}
    </span>
  );
}

export function CommunityReportDetailPage() {
  const { id: idParam } = useParams<{ id: string }>();
  const id = idParam ? Number(idParam) : undefined;
  const queryClient = useQueryClient();

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['community-report', id],
    queryFn: () => getCommunityReport(id!),
    enabled: !!id,
  });

  const processMutation = useMutation({
    mutationFn: () => processCommunityReport(id!),
    onSuccess: (result) => {
      toast.success(`Processed: ${result.matched} matched, ${result.review_queue} in review`);
      queryClient.invalidateQueries({ queryKey: ['community-report', id] });
    },
    onError: (err: unknown) => {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Failed to reprocess');
    },
  });

  const backLink = (
    <Link
      to="/community-reports"
      aria-label="Back to community reports"
      className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
    >
      <ArrowLeft size={18} aria-hidden="true" />
    </Link>
  );

  if (isLoading) {
    return (
      <div className="flex h-screen flex-col">
        <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
          <div className="flex items-center gap-2">{backLink}<h1 className="text-lg font-bold text-foreground">Report</h1></div>
        </header>
        <main className="flex flex-1 items-center justify-center">
          <div className="text-center">
            <Loader2 size={24} className="mx-auto animate-spin text-primary" aria-hidden="true" />
            <p className="mt-2 text-sm text-muted-foreground">Loading report…</p>
          </div>
        </main>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex h-screen flex-col">
        <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
          <div className="flex items-center gap-2">{backLink}<h1 className="text-lg font-bold text-foreground">Report</h1></div>
        </header>
        <main className="flex flex-1 items-center justify-center p-4">
          <div className="rounded-2xl border border-border bg-card p-6 text-center" role="alert">
            <p className="mb-3 text-foreground">Report not found or failed to load.</p>
            <button
              type="button"
              onClick={() => refetch()}
              className="rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              Retry
            </button>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="mx-auto flex max-w-2xl items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            {backLink}
            <h1 className="truncate text-lg font-bold text-foreground">
              {data.event_title ?? 'Community Report'}
            </h1>
          </div>
          <button
            type="button"
            onClick={() => processMutation.mutate()}
            disabled={processMutation.isPending || !id}
            aria-label="Re-process report"
            className="flex shrink-0 items-center gap-1.5 rounded-xl border border-border px-3 py-2 text-sm font-semibold text-foreground hover:bg-muted disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {processMutation.isPending ? (
              <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCw size={14} aria-hidden="true" />
            )}
            Re-process
          </button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-2xl flex-1 space-y-4 p-4 pb-24">
        {/* Header card */}
        <section aria-label="Report overview" className="rounded-2xl border border-border bg-card p-5">
          <div className="flex flex-wrap items-start justify-between gap-2 mb-3">
            <div>
              <p className="text-xl font-bold text-foreground">{data.event_title ?? 'No event'}</p>
              <p className="text-sm text-muted-foreground">{data.date_of_activity}</p>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <StatusBadge status={data.status} />
              <MatchStatusBadge matchStatus={data.match_status} />
            </div>
          </div>
          <div className="divide-y divide-border text-sm">
            {data.zone && (
              <div className="flex gap-3 py-1.5">
                <span className="w-32 shrink-0 text-xs font-medium text-muted-foreground">Zone</span>
                <span className="text-foreground">{data.zone}</span>
              </div>
            )}
            {data.submitted_by_name && (
              <div className="flex gap-3 py-1.5">
                <span className="w-32 shrink-0 text-xs font-medium text-muted-foreground">Submitted by</span>
                <span className="text-foreground">{data.submitted_by_name}</span>
              </div>
            )}
            {data.event_leader_name && (
              <div className="flex gap-3 py-1.5">
                <span className="w-32 shrink-0 text-xs font-medium text-muted-foreground">Leader</span>
                <span className="text-foreground">{data.event_leader_name}</span>
              </div>
            )}
            <div className="flex gap-3 py-1.5">
              <span className="w-32 shrink-0 text-xs font-medium text-muted-foreground">Match stats</span>
              <span className="text-foreground">{data.matched_count} matched · {data.review_count} in review</span>
            </div>
          </div>
        </section>

        {/* Matched contacts */}
        {data.matched_contacts.length > 0 && (
          <section aria-label="Matched contacts" className="rounded-2xl border border-border bg-card p-5">
            <h2 className="mb-3 text-sm font-bold uppercase tracking-wide text-muted-foreground">
              Matched Contacts ({data.matched_contacts.length})
            </h2>
            <ul className="space-y-1.5">
              {data.matched_contacts.map((c) => (
                <li key={c.contact_id}>
                  <Link
                    to={`/contacts/${c.contact_id}`}
                    className="flex items-center justify-between rounded-xl border border-border px-3 py-2 text-sm font-medium text-foreground hover:bg-muted hover:border-primary/50 focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
                  >
                    {c.display_name}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* Pending review */}
        {data.review_queue_items.length > 0 && (
          <section aria-label="Pending review items" className="rounded-2xl border border-border bg-card p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">
                Pending Review ({data.review_queue_items.length})
              </h2>
              <Link
                to="/name-match/review"
                className="text-xs font-semibold text-primary hover:underline focus:outline-none"
              >
                Review all
              </Link>
            </div>
            <ul className="space-y-2">
              {data.review_queue_items.map((item) => (
                <li key={item.id} className="flex items-center justify-between gap-2 rounded-xl border border-border px-3 py-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">{item.raw_name}</p>
                    <p className="truncate text-xs text-muted-foreground">{item.normalized_name}</p>
                  </div>
                  <ReviewStatusBadge status={item.status} />
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* Qualitative info */}
        {(data.topics || data.prayer_items || data.remarks) && (
          <section aria-label="Notes" className="rounded-2xl border border-border bg-card p-5">
            <h2 className="mb-3 text-sm font-bold uppercase tracking-wide text-muted-foreground">Notes</h2>
            <div className="space-y-4">
              {data.topics && (
                <details open>
                  <summary className="cursor-pointer text-sm font-semibold text-foreground">Topics</summary>
                  <p className="mt-2 text-sm text-muted-foreground whitespace-pre-wrap">{data.topics}</p>
                </details>
              )}
              {data.prayer_items && (
                <details open>
                  <summary className="cursor-pointer text-sm font-semibold text-foreground">Prayer Items</summary>
                  <p className="mt-2 text-sm text-muted-foreground whitespace-pre-wrap">{data.prayer_items}</p>
                </details>
              )}
              {data.remarks && (
                <details open>
                  <summary className="cursor-pointer text-sm font-semibold text-foreground">Remarks</summary>
                  <p className="mt-2 text-sm text-muted-foreground whitespace-pre-wrap">{data.remarks}</p>
                </details>
              )}
            </div>
          </section>
        )}

        {/* Empty state for no content */}
        {data.matched_contacts.length === 0 && data.review_queue_items.length === 0 && (
          <div className="rounded-2xl border border-border bg-card p-6 text-center" aria-live="polite">
            <p className="text-sm font-semibold text-foreground">No match data yet</p>
            <p className="mt-1 text-xs text-muted-foreground">Click Re-process to start name matching.</p>
          </div>
        )}
      </main>
    </div>
  );
}
