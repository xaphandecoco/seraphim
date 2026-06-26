import { useState } from 'react';
import { useParams, Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, Download } from 'lucide-react';
import { toast } from 'sonner';
import { getImportBatch, listImportRows, downloadImportReport } from '@/services/imports';
import type { ImportBatchDetail, ImportRowResult, ImportRowOutcome } from '@/types/imports';
import { Pagination } from '@/components/ui/Pagination';
import { LoadingState, EmptyState, ErrorState } from '@/components/ui/StateViews';

const PAGE_SIZE = 25;

const OUTCOME_TABS: { value: string; label: string }[] = [
  { value: '', label: 'All' },
  { value: 'created', label: 'Created' },
  { value: 'updated', label: 'Updated' },
  { value: 'skipped', label: 'Skipped' },
  { value: 'error', label: 'Errors' },
  { value: 'review', label: 'Reviews' },
];

function OutcomeBadge({ outcome }: { outcome: ImportRowOutcome }) {
  const styles: Record<ImportRowOutcome, string> = {
    created: 'bg-primary/10 text-primary',
    updated: 'bg-secondary text-secondary-foreground',
    skipped: 'bg-muted text-foreground/50',
    error: 'bg-destructive/10 text-destructive',
    review: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400',
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${styles[outcome] ?? 'bg-muted text-foreground/50'}`}
    >
      {outcome}
    </span>
  );
}

function BatchStatusBadge({ status }: { status: string }) {
  if (status === 'running') {
    return (
      <span className="inline-flex animate-pulse items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-primary text-primary">
        running
      </span>
    );
  }
  if (status === 'completed') {
    return (
      <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold bg-primary/10 text-primary">
        completed
      </span>
    );
  }
  if (status === 'failed') {
    return (
      <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold bg-destructive/10 text-destructive">
        failed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold bg-muted text-muted-foreground">
      {status}
    </span>
  );
}

function StatChip({
  label,
  value,
  className = '',
}: {
  label: string;
  value: number;
  className?: string;
}) {
  return (
    <div
      className={`flex flex-col items-center rounded-xl border border-border bg-card px-4 py-2 ${className}`}
    >
      <span className="text-lg font-bold text-foreground">{value}</span>
      <span className="text-xs text-foreground/50">{label}</span>
    </div>
  );
}

function RowCard({ row }: { row: ImportRowResult }) {
  return (
    <div className="space-y-1 rounded-2xl border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-foreground/50">Row {row.row_number}</span>
        <OutcomeBadge outcome={row.outcome} />
      </div>
      {row.entity_id != null && (
        <p className="text-xs text-foreground">Entity #{row.entity_id}</p>
      )}
      {row.external_id && (
        <p className="text-xs text-foreground/70">ID: {row.external_id}</p>
      )}
      {row.message && (
        <p className="break-words text-xs text-foreground/70">{row.message}</p>
      )}
    </div>
  );
}

function PageHeader() {
  return (
    <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
      <div className="mx-auto max-w-4xl">
        <h1 className="text-lg font-bold text-foreground">Import Report</h1>
      </div>
    </header>
  );
}

export function ImportReportPage() {
  const { batchId } = useParams<{ batchId: string }>();
  const id = Number(batchId);
  const [searchParams, setSearchParams] = useSearchParams();
  const outcome = searchParams.get('outcome') ?? '';
  const [page, setPage] = useState(1);
  const [downloading, setDownloading] = useState(false);

  const {
    data: batch,
    isLoading: batchLoading,
    isError: batchError,
    refetch: refetchBatch,
  } = useQuery({
    queryKey: ['import-batch', id] as const,
    queryFn: () => getImportBatch(id),
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 3000 : false,
    enabled: !isNaN(id),
  });

  const {
    data: rowsData,
    isLoading: rowsLoading,
    isError: rowsError,
    refetch: refetchRows,
  } = useQuery({
    queryKey: ['import-rows', id, outcome, page] as const,
    queryFn: () =>
      listImportRows(id, {
        outcome: outcome || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
    refetchInterval: () => (batch?.status === 'running' ? 3000 : false),
    enabled: !isNaN(id),
  });

  const handleOutcomeChange = (value: string) => {
    setPage(1);
    if (value) {
      setSearchParams({ outcome: value });
    } else {
      setSearchParams({});
    }
  };

  const handleDownload = async () => {
    setDownloading(true);
    try {
      await downloadImportReport(id);
    } catch (err) {
      let detail: string | undefined;
      const data = (err as { response?: { data?: unknown } })?.response?.data;
      if (data instanceof Blob) {
        try {
          detail = JSON.parse(await data.text())?.detail;
        } catch {
          /* non-JSON blob — fall back to generic message */
        }
      } else {
        detail = (data as { detail?: string })?.detail;
      }
      toast.error(detail ?? 'Failed to download report CSV.');
    } finally {
      setDownloading(false);
    }
  };

  if (batchLoading) {
    return (
      <div className="flex min-h-screen flex-col bg-background">
        <PageHeader />
        <main className="mx-auto w-full max-w-4xl flex-1 p-4">
          <LoadingState message="Loading batch details…" />
        </main>
      </div>
    );
  }

  if (batchError || !batch) {
    return (
      <div className="flex min-h-screen flex-col bg-background">
        <PageHeader />
        <main className="mx-auto w-full max-w-4xl flex-1 p-4">
          <ErrorState
            message="Failed to load import batch."
            onRetry={() => refetchBatch()}
          />
        </main>
      </div>
    );
  }

  const typedBatch = batch as ImportBatchDetail;

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <PageHeader />

      <main className="mx-auto w-full max-w-4xl flex-1 space-y-4 p-4 pb-24">
        {/* Back link */}
        <Link
          to="/imports"
          className="inline-flex items-center gap-1 text-sm text-foreground/60 hover:text-foreground"
        >
          <ChevronLeft size={16} aria-hidden="true" />
          Back to Imports
        </Link>

        {/* Summary card */}
        <section
          className="space-y-3 rounded-2xl border border-border bg-card p-5"
          aria-label="Batch summary"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="truncate text-base font-bold text-foreground">
                {typedBatch.source_filename ?? `Batch #${id}`}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span className="rounded-full border border-border px-2.5 py-0.5 text-xs font-medium text-foreground/70">
                  {typedBatch.entity}
                </span>
                <span className="rounded-full border border-border px-2.5 py-0.5 text-xs font-medium text-foreground/70">
                  {typedBatch.mode}
                </span>
                <BatchStatusBadge status={typedBatch.status} />
              </div>
            </div>
            <button
              type="button"
              onClick={handleDownload}
              disabled={downloading}
              className="flex items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-2 text-xs font-semibold text-foreground hover:bg-primary/10 disabled:opacity-50"
              aria-label="Download report CSV"
            >
              <Download size={14} aria-hidden="true" />
              {downloading ? 'Downloading…' : 'Download report CSV'}
            </button>
          </div>

          <div className="flex flex-wrap gap-2 text-xs text-foreground/50">
            {typedBatch.started_at && (
              <span>Started: {new Date(typedBatch.started_at).toLocaleString()}</span>
            )}
            {typedBatch.finished_at && (
              <span>Completed: {new Date(typedBatch.finished_at).toLocaleString()}</span>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            <StatChip
              label="Created"
              value={typedBatch.created_count}
              className="text-primary"
            />
            <StatChip label="Updated" value={typedBatch.updated_count} />
            <StatChip label="Skipped" value={typedBatch.skipped_count} />
            {typedBatch.error_count > 0 && (
              <StatChip
                label="Errors"
                value={typedBatch.error_count}
                className="text-destructive"
              />
            )}
            {typedBatch.review_count > 0 && (
              <StatChip
                label="Review"
                value={typedBatch.review_count}
                className="text-amber-700 dark:text-amber-400"
              />
            )}
          </div>
        </section>

        {/* Outcome filter tabs */}
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Filter by outcome">
          {OUTCOME_TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              role="tab"
              aria-selected={outcome === tab.value}
              onClick={() => handleOutcomeChange(tab.value)}
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${
                outcome === tab.value
                  ? 'bg-primary text-primary-foreground'
                  : 'border border-border bg-card text-foreground/70 hover:bg-primary/10'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Row results */}
        {rowsLoading && <LoadingState message="Loading rows…" />}

        {rowsError && (
          <ErrorState
            message="Failed to load row results."
            onRetry={() => refetchRows()}
          />
        )}

        {!rowsLoading && !rowsError && rowsData && (
          <>
            {rowsData.items.length === 0 ? (
              <EmptyState title="No rows match this filter." />
            ) : (
              <>
                {/* Mobile cards */}
                <div className="block space-y-2 md:hidden">
                  {rowsData.items.map((row) => (
                    <RowCard key={row.id} row={row} />
                  ))}
                </div>

                {/* Desktop table */}
                <div className="hidden w-full overflow-x-auto rounded-2xl border border-border bg-card md:block">
                  <table className="w-full text-sm text-foreground">
                    <thead>
                      <tr className="border-b border-border bg-card">
                        <th
                          scope="col"
                          className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-foreground/60"
                        >
                          Row
                        </th>
                        <th
                          scope="col"
                          className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-foreground/60"
                        >
                          Outcome
                        </th>
                        <th
                          scope="col"
                          className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-foreground/60"
                        >
                          Entity ID
                        </th>
                        <th
                          scope="col"
                          className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-foreground/60"
                        >
                          Message
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {rowsData.items.map((row) => (
                        <tr key={row.id} className="border-b border-border last:border-0">
                          <td className="px-4 py-3 text-xs text-foreground/60">
                            {row.row_number}
                          </td>
                          <td className="px-4 py-3">
                            <OutcomeBadge outcome={row.outcome} />
                          </td>
                          <td className="px-4 py-3 text-xs text-foreground">
                            {row.entity_id != null ? `#${row.entity_id}` : '—'}
                          </td>
                          <td className="max-w-sm break-words px-4 py-3 text-xs text-foreground/70">
                            {row.message ?? '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <Pagination
                  page={page}
                  pageSize={PAGE_SIZE}
                  total={rowsData.total}
                  onPageChange={setPage}
                />
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}
