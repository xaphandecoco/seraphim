import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Database } from 'lucide-react';
import { listBatches } from '@/services/migration';
import type { ImportBatch, ImportBatchListResponse, ImportBatchStatus } from '@/types/migration';
import { DataTable } from '@/components/ui/DataTable';
import type { Column } from '@/components/ui/DataTable';
import { ErrorState } from '@/components/ui/StateViews';

function StatusBadge({ status }: { status: ImportBatchStatus }) {
  if (status === 'running') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-primary animate-pulse text-primary">
        <span className="h-1.5 w-1.5 rounded-full bg-primary" aria-hidden="true" />
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

/** Entity chip — shows which data phase the batch covers (contacts/events/participants/links) */
function EntityChip({ entity }: { entity: string }) {
  return (
    <span className="inline-flex items-center rounded-full border border-border px-2.5 py-0.5 text-xs font-medium text-foreground/70">
      {entity}
    </span>
  );
}

/**
 * Mode chip — dry_run is rendered in amber to distinguish it from a real live import.
 * Confusing a dry-run for a live import is dangerous, so the visual treatment is intentionally
 * different.
 */
function ModeChip({ mode }: { mode: string }) {
  if (mode === 'dry_run') {
    return (
      <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400">
        dry_run
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full border border-border px-2.5 py-0.5 text-xs font-medium text-foreground/70">
      {mode}
    </span>
  );
}

const CLI_EXAMPLE = `python scripts/migrate_civicrm.py \\
  --file export.xlsx \\
  --mode live`;

const columns: Column<ImportBatch>[] = [
  {
    key: 'source_filename',
    header: 'File',
    render: (row) => (
      <span className="font-medium text-foreground truncate max-w-[200px] block" title={row.source_filename ?? '—'}>
        {row.source_filename ?? '—'}
      </span>
    ),
  },
  {
    key: 'entity',
    header: 'Entity / Mode',
    render: (row) => (
      <div className="flex flex-wrap items-center gap-1">
        <EntityChip entity={row.entity} />
        <ModeChip mode={row.mode} />
      </div>
    ),
  },
  {
    key: 'status',
    header: 'Status',
    render: (row) => <StatusBadge status={row.status as ImportBatchStatus} />,
  },
  {
    key: 'total_rows',
    header: 'Rows',
    render: (row) => (
      <span className="text-xs text-foreground/70">
        {row.total_rows}
      </span>
    ),
  },
  {
    key: 'started_at',
    header: 'Started',
    render: (row) => (
      <span className="text-xs text-foreground/60">
        {row.started_at ? new Date(row.started_at).toLocaleString() : '—'}
      </span>
    ),
  },
];

export function MigrationPage() {
  const navigate = useNavigate();

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['migration-batches'] as const,
    queryFn: (): Promise<ImportBatchListResponse> => listBatches(),
    select: (response): ImportBatch[] => response.items,
    refetchInterval: (query) => {
      // query.state.data holds the RAW queryFn result (the ImportBatchListResponse
      // wrapper), NOT the select-transformed array — so poll off .items.
      const items = query.state.data?.items;
      if (items?.some((b) => b.status === 'running')) {
        return 5000;
      }
      return false;
    },
  });

  const handleRowClick = (batch: ImportBatch) => {
    navigate(`/settings/migration/${batch.id}`);
  };

  const emptyStateNode = (
    <div className="flex flex-col items-center justify-center py-16 text-foreground/50">
      <Database size={40} className="mb-3 opacity-40" aria-hidden="true" />
      <p className="text-sm font-medium">No migration batches yet</p>
      <p className="mt-1 text-xs">Run the CLI to import data:</p>
      <pre className="mt-3 rounded-xl bg-muted px-4 py-3 text-left text-xs font-mono text-foreground whitespace-pre-wrap">
        {CLI_EXAMPLE}
      </pre>
    </div>
  );

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="mx-auto flex max-w-4xl items-center gap-3">
          <Database size={20} className="text-primary shrink-0" aria-hidden="true" />
          <h1 className="text-lg font-bold text-foreground">Data Migration</h1>
        </div>
      </header>

      <main className="mx-auto w-full max-w-4xl flex-1 p-4 pb-24">
        {isError ? (
          <ErrorState
            message="Failed to load migration batches."
            onRetry={() => refetch()}
          />
        ) : (
          <DataTable<ImportBatch>
            columns={columns}
            rows={data ?? []}
            getRowKey={(row) => row.id}
            onRowClick={handleRowClick}
            isLoading={isLoading}
            emptyState={emptyStateNode}
          />
        )}
      </main>
    </div>
  );
}
