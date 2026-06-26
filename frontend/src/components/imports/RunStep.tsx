import { useEffect } from 'react';
import { useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { CheckCircle, ArrowRight } from 'lucide-react';
import { Link } from 'react-router-dom';
import { runImport } from '@/services/imports';
import { LoadingState, ErrorState } from '@/components/ui/StateViews';
import type { MatchKey, ConflictPolicy, RunCounts } from '@/types/imports';

interface RunStepProps {
  batchId: number;
  columnMap: Record<string, string>;
  matchKey: MatchKey;
  conflictPolicy: ConflictPolicy;
  selectedSheet: string | null;
  targetEventId: number | null;
  runCounts: RunCounts | null;
  runElapsedMs: number | null;
  runBatchId: number | null;
  onRunComplete: (args: { batchId: number; counts: RunCounts; elapsedMs: number }) => void;
  onBack: () => void;
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

export function RunStep({
  batchId,
  columnMap,
  matchKey,
  conflictPolicy,
  selectedSheet,
  targetEventId,
  runCounts,
  runElapsedMs,
  runBatchId,
  onRunComplete,
  onBack,
}: RunStepProps) {
  const buildRunBody = () => ({
    column_map: columnMap,
    match_key: matchKey,
    conflict_policy: conflictPolicy,
    ...(selectedSheet ? { sheet: selectedSheet } : {}),
    ...(targetEventId ? { target_event_id: targetEventId } : {}),
  });

  const runMutation = useMutation({
    mutationFn: () => runImport(batchId, buildRunBody()),
    onSuccess: (data) => {
      const c = data.counts;
      toast.success(
        `Import complete — Created ${c.created} · Updated ${c.updated} · Skipped ${c.skipped} · ${(data.elapsed_ms / 1000).toFixed(1)} s`,
      );
      onRunComplete({
        batchId,
        counts: c,
        elapsedMs: data.elapsed_ms,
      });
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? 'Import run failed. Some rows may have been committed.');
    },
  });

  // Auto-trigger on mount (user already confirmed in PreviewStep)
  useEffect(() => {
    if (!runCounts && !runMutation.isPending && !runMutation.isSuccess && !runMutation.isError) {
      runMutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Running ───────────────────────────────────────────────────────────────
  if (runMutation.isPending) {
    return (
      <div className="space-y-4">
        <LoadingState message="Running import — please wait…" />
        <p className="text-center text-xs text-foreground/50">
          Large files commit in batches of ~500 rows. This may take a moment.
        </p>
      </div>
    );
  }

  // ── Error ─────────────────────────────────────────────────────────────────
  if (runMutation.isError) {
    return (
      <div className="space-y-4">
        <ErrorState
          message="Import encountered an error. Some rows may have been committed."
          onRetry={() => runMutation.mutate()}
        />
        <div className="flex justify-start">
          <button
            type="button"
            onClick={onBack}
            className="rounded-xl border border-border bg-background px-5 py-2.5 text-sm font-semibold text-foreground hover:bg-primary/10"
          >
            Back to preview
          </button>
        </div>
      </div>
    );
  }

  // ── Success ───────────────────────────────────────────────────────────────
  const counts = runCounts ?? runMutation.data?.counts;
  const elapsed = runElapsedMs ?? runMutation.data?.elapsed_ms;
  const reportBatchId = runBatchId ?? batchId;

  if (!counts) return null;

  return (
    <div className="space-y-6">
      <div className="flex flex-col items-center gap-3 py-6">
        <CheckCircle
          size={48}
          className="text-primary"
          aria-hidden="true"
        />
        <h2 className="text-lg font-bold text-foreground">Import complete</h2>
        {elapsed !== null && elapsed !== undefined && (
          <p className="text-xs text-foreground/50">
            Finished in {(elapsed / 1000).toFixed(1)} s
          </p>
        )}
      </div>

      {/* Result counts */}
      <div className="flex flex-wrap justify-center gap-3">
        <StatChip label="Created" value={counts.created} className="text-primary" />
        <StatChip label="Updated" value={counts.updated} />
        <StatChip label="Skipped" value={counts.skipped} />
        {counts.review > 0 && (
          <StatChip
            label="Review"
            value={counts.review}
            className="text-amber-700 dark:text-amber-400"
          />
        )}
        {counts.errors > 0 && (
          <StatChip label="Errors" value={counts.errors} className="text-destructive" />
        )}
      </div>

      {/* Link to full report */}
      <div className="flex flex-col items-center gap-3 pt-2">
        <Link
          to={`/imports/${reportBatchId}`}
          className="flex items-center gap-2 rounded-xl bg-primary px-6 py-2.5 text-sm font-bold text-primary-foreground shadow-sm hover:bg-primary/85"
        >
          View full report
          <ArrowRight size={16} aria-hidden="true" />
        </Link>
        <Link
          to="/imports"
          className="text-xs text-foreground/60 hover:text-foreground underline"
        >
          Back to imports list
        </Link>
      </div>
    </div>
  );
}
