import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { FileSpreadsheet, Plus } from 'lucide-react';
import { listImportBatches } from '@/services/imports';
import type { ImportBatch, ImportBatchStatus } from '@/types/imports';
import { DataTable } from '@/components/ui/DataTable';
import type { Column } from '@/components/ui/DataTable';
import { Pagination } from '@/components/ui/Pagination';
import { ErrorState, EmptyState } from '@/components/ui/StateViews';

const PAGE_SIZE = 25;

function StatusBadge({ status }: { status: string }) {
  if (status === 'running') {
    return (
      <span className="inline-flex animate-pulse items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-primary text-primary">
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

const columns: Column<ImportBatch>[] = [
  {
    key: 'source_filename',
    header: 'File',
    render: (row) => (
      <span
        className="block max-w-[180px] truncate font-medium text-foreground"
        title={row.source_filename ?? '—'}
      >
        {row.source_filename ?? '—'}
      </span>
    ),
  },
  {
    key: 'entity',
    header: 'Entity',
    render: (row) => (
      <span className="inline-flex items-center rounded-full border border-border px-2.5 py-0.5 text-xs font-medium text-foreground/70">
        {row.entity}
      </span>
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
      <span className="text-xs text-foreground/70">{row.total_rows}</span>
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

export function ImportsListPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['import-batches', { page }] as const,
    queryFn: () =>
      listImportBatches({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }),
    refetchInterval: (query) => {
      const items = query.state.data?.items;
      return items?.some((b) => b.status === 'running') ? 5000 : false;
    },
  });

  const rows = data?.items ?? [];
  const total = data?.total ?? 0;

  const emptyStateNode = (
    <EmptyState
      icon={FileSpreadsheet}
      title="No imports yet"
      description="Import a CSV or Excel file to get started."
    />
  );

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <FileSpreadsheet size={20} className="shrink-0 text-primary" aria-hidden="true" />
            <h1 className="text-lg font-bold text-foreground">Imports</h1>
          </div>
          <button
            type="button"
            onClick={() => navigate('/imports/new')}
            className="flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-xs font-bold text-primary-foreground shadow-sm hover:bg-primary/85"
          >
            <Plus size={14} aria-hidden="true" />
            New import
          </button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-4xl flex-1 space-y-4 p-4 pb-24">
        {isError ? (
          <ErrorState
            message="Failed to load import runs."
            onRetry={() => refetch()}
          />
        ) : (
          <>
            <DataTable<ImportBatch>
              columns={columns}
              rows={rows}
              getRowKey={(row) => row.id}
              onRowClick={(row) => navigate(`/imports/${row.id}`)}
              isLoading={isLoading}
              emptyState={emptyStateNode}
            />
            {total > PAGE_SIZE && (
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                total={total}
                onPageChange={setPage}
              />
            )}
          </>
        )}
      </main>
    </div>
  );
}
