import { useState, useEffect } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { runPreview, listPreviewRows } from '@/services/imports';
import { LoadingState, EmptyState, ErrorState } from '@/components/ui/StateViews';
import { Pagination } from '@/components/ui/Pagination';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type {
  MatchKey,
  ConflictPolicy,
  PreviewCounts,
  ImportRowResult,
} from '@/types/imports';

interface PreviewStepProps {
  batchId: number;
  columnMap: Record<string, string>;
  matchKey: MatchKey;
  conflictPolicy: ConflictPolicy;
  selectedSheet: string | null;
  targetEventId: number | null;
  previewCounts: PreviewCounts | null;
  onPreviewCounts: (counts: PreviewCounts) => void;
  onConflictPolicyChange: (policy: ConflictPolicy) => void;
  onBack: () => void;
  onNext: () => void;
}

const PAGE_SIZE = 25;

const OUTCOME_TABS = [
  { value: '', label: 'All' },
  { value: 'created', label: 'New' },
  { value: 'updated', label: 'Match' },
  { value: 'skipped', label: 'Skip' },
  { value: 'review', label: 'Ambiguous' },
  { value: 'error', label: 'Errors' },
];

function OutcomeBadge({ outcome }: { outcome: string }) {
  const styles: Record<string, string> = {
    created: 'bg-primary/10 text-primary',
    updated: 'bg-secondary text-secondary-foreground',
    skipped: 'bg-muted text-foreground/50',
    review: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400',
    error: 'bg-destructive/10 text-destructive',
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${styles[outcome] ?? 'bg-muted text-foreground/50'}`}
    >
      {outcome}
    </span>
  );
}

function CountChip({
  label,
  value,
  variant = 'default',
}: {
  label: string;
  value: number;
  variant?: 'primary' | 'secondary' | 'muted' | 'destructive' | 'default';
}) {
  const styles: Record<string, string> = {
    primary: 'bg-primary/10 text-primary border-primary/20',
    secondary: 'bg-secondary text-secondary-foreground border-secondary/20',
    muted: 'bg-muted text-foreground/50 border-border',
    destructive: 'bg-destructive/10 text-destructive border-destructive/20',
    default: 'bg-card text-foreground border-border',
  };
  return (
    <div
      className={`flex flex-col items-center rounded-xl border px-4 py-2 ${styles[variant]}`}
    >
      <span className="text-lg font-bold">{value}</span>
      <span className="text-xs">{label}</span>
    </div>
  );
}

function RowCard({ row }: { row: ImportRowResult }) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4 space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-foreground/50">Row {row.row_number}</span>
        <OutcomeBadge outcome={row.outcome} />
      </div>
      {row.external_id && (
        <p className="text-xs text-foreground">ID: {row.external_id}</p>
      )}
      {row.message && (
        <p className="text-xs text-foreground/70 break-words">{row.message}</p>
      )}
    </div>
  );
}

export function PreviewStep({
  batchId,
  columnMap,
  matchKey,
  conflictPolicy,
  selectedSheet,
  targetEventId,
  previewCounts,
  onPreviewCounts,
  onConflictPolicyChange,
  onBack,
  onNext,
}: PreviewStepProps) {
  const [outcomeFilter, setOutcomeFilter] = useState('');
  const [page, setPage] = useState(1);
  const [showConfirm, setShowConfirm] = useState(false);

  const buildPreviewBody = () => ({
    column_map: columnMap,
    match_key: matchKey,
    conflict_policy: conflictPolicy,
    ...(selectedSheet ? { sheet: selectedSheet } : {}),
    ...(targetEventId ? { target_event_id: targetEventId } : {}),
  });

  const previewMutation = useMutation({
    mutationFn: () => runPreview(batchId, buildPreviewBody()),
    onSuccess: (data) => {
      onPreviewCounts(data.counts);
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? 'Preview failed. Check your column mapping.');
    },
  });

  // Run preview automatically on mount if no counts yet
  useEffect(() => {
    if (!previewCounts) {
      previewMutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-run preview when conflict policy changes
  const handlePolicyChange = (policy: ConflictPolicy) => {
    onConflictPolicyChange(policy);
    // defer to avoid stale closure issue
    setTimeout(() => previewMutation.mutate(), 0);
  };

  const {
    data: rowsData,
    isLoading: rowsLoading,
    isError: rowsError,
    refetch: refetchRows,
  } = useQuery({
    queryKey: ['import-preview-rows', batchId, outcomeFilter, page] as const,
    queryFn: () =>
      listPreviewRows(batchId, {
        outcome: outcomeFilter || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
    enabled: !!previewCounts,
  });

  const isPreviewing = previewMutation.isPending;

  const counts = previewMutation.data?.counts ?? previewCounts;

  const confirmMessage = counts
    ? `Run import: ${counts.would_create} new · ${counts.would_update} updates · ${counts.would_skip} skipped?`
    : 'Run this import?';

  return (
    <div className="space-y-6">
      {/* Conflict policy segmented control */}
      <div>
        <p className="mb-2 text-sm font-semibold text-foreground">Conflict policy</p>
        <div
          className="flex overflow-hidden rounded-xl border border-border bg-background"
          role="group"
          aria-label="Conflict policy"
        >
          {(
            [
              { value: 'skip', label: 'Skip existing' },
              { value: 'update', label: 'Update all' },
              { value: 'fill', label: 'Fill empty only' },
            ] as { value: ConflictPolicy; label: string }[]
          ).map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => handlePolicyChange(opt.value)}
              className={`flex-1 py-2.5 text-xs font-semibold transition-colors ${
                conflictPolicy === opt.value
                  ? 'bg-primary text-primary-foreground'
                  : 'text-foreground hover:bg-primary/10'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Preview loading */}
      {isPreviewing && <LoadingState message="Running preview…" />}

      {/* Counts strip */}
      {counts && !isPreviewing && (
        <div className="flex flex-wrap gap-2">
          <CountChip label="New" value={counts.would_create} variant="primary" />
          <CountChip label="Will update" value={counts.would_update} variant="secondary" />
          <CountChip label="Will skip" value={counts.would_skip} variant="muted" />
          <CountChip label="Ambiguous" value={counts.ambiguous} variant="destructive" />
          <CountChip label="Errors" value={counts.errors} variant="destructive" />
        </div>
      )}

      {counts?.ambiguous !== undefined && counts.ambiguous > 0 && (
        <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800 dark:border-amber-900/40 dark:bg-amber-900/20 dark:text-amber-400">
          {counts.ambiguous} participant row(s) with ambiguous names will go to the name-match review queue.
        </p>
      )}

      {/* Outcome filter tabs */}
      {counts && !isPreviewing && (
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Filter by outcome">
          {OUTCOME_TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              role="tab"
              aria-selected={outcomeFilter === tab.value}
              onClick={() => { setOutcomeFilter(tab.value); setPage(1); }}
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${
                outcomeFilter === tab.value
                  ? 'bg-primary text-primary-foreground'
                  : 'border border-border bg-card text-foreground/70 hover:bg-primary/10'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      )}

      {/* Preview rows */}
      {rowsLoading && <LoadingState message="Loading preview rows…" />}

      {rowsError && (
        <ErrorState message="Failed to load preview rows." onRetry={() => refetchRows()} />
      )}

      {!rowsLoading && !rowsError && rowsData && (
        <>
          {rowsData.items.length === 0 ? (
            <EmptyState title="No data rows found in this file." />
          ) : (
            <>
              {/* Mobile */}
              <div className="block space-y-2 md:hidden">
                {rowsData.items.map((row) => (
                  <RowCard key={row.id} row={row} />
                ))}
              </div>

              {/* Desktop */}
              <div className="hidden md:block overflow-x-auto rounded-2xl border border-border bg-card">
                <table className="w-full text-sm text-foreground">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-foreground/60">Row</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-foreground/60">Outcome</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-foreground/60">ID</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-foreground/60">Message</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rowsData.items.map((row) => (
                      <tr key={row.id} className="border-b border-border last:border-0">
                        <td className="px-4 py-3 text-xs text-foreground/60">{row.row_number}</td>
                        <td className="px-4 py-3"><OutcomeBadge outcome={row.outcome} /></td>
                        <td className="px-4 py-3 text-xs text-foreground">{row.external_id ?? '—'}</td>
                        <td className="px-4 py-3 text-xs text-foreground/70 max-w-sm break-words">
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

      {/* Footer */}
      <div className="flex justify-between pt-2">
        <button
          type="button"
          onClick={onBack}
          className="rounded-xl border border-border bg-background px-5 py-2.5 text-sm font-semibold text-foreground hover:bg-primary/10"
        >
          Back
        </button>
        <button
          type="button"
          onClick={() => setShowConfirm(true)}
          disabled={isPreviewing || !counts}
          className="rounded-xl bg-primary px-6 py-2.5 text-sm font-bold text-primary-foreground shadow-sm transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Run import
        </button>
      </div>

      {showConfirm && (
        <ConfirmDialog
          message={confirmMessage}
          confirmLabel="Run import"
          onConfirm={() => { setShowConfirm(false); onNext(); }}
          onCancel={() => setShowConfirm(false)}
        />
      )}
    </div>
  );
}
