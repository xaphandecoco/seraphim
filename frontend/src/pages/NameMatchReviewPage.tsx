import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import { getReviewQueue, skipQueueItem, unmatchQueueItem } from '@/services/nameMatch';
import { ResolveMatchModal } from '@/components/name-match/ResolveMatchModal';
import type { ReviewQueueItem, ReviewQueueStatus } from '@/types/nameMatch';
import type { ReviewQueueFilters } from '@/services/nameMatch';

const PAGE_SIZE = 25;

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'All statuses' },
  { value: 'pending', label: 'Pending' },
  { value: 'matched', label: 'Matched' },
  { value: 'unmatched', label: 'Unmatched' },
  { value: 'skipped', label: 'Skipped' },
];

function StatusBadge({ status }: { status: ReviewQueueStatus }) {
  const styles: Record<ReviewQueueStatus, string> = {
    pending: 'bg-amber-50 text-amber-700 border-amber-200',
    matched: 'bg-green-50 text-green-700 border-green-200',
    unmatched: 'bg-red-50 text-red-600 border-red-200',
    skipped: 'bg-gray-100 text-gray-500 border-gray-200',
  };
  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${styles[status] ?? 'bg-muted text-muted-foreground border-border'}`}>
      {status}
    </span>
  );
}

function LoadingSkeleton() {
  return (
    <ul className="space-y-3" aria-busy="true" aria-label="Loading">
      {Array.from({ length: 5 }).map((_, i) => (
        <li key={i} className="animate-pulse rounded-2xl border border-border bg-card p-4">
          <div className="mb-2 h-4 w-48 rounded bg-muted" />
          <div className="mb-3 h-3 w-32 rounded bg-muted" />
          <div className="flex gap-2">
            <div className="h-8 w-20 rounded-xl bg-muted" />
            <div className="h-8 w-16 rounded-xl bg-muted" />
            <div className="h-8 w-24 rounded-xl bg-muted" />
          </div>
        </li>
      ))}
    </ul>
  );
}

function QueueCard({
  item,
  onResolve,
  onSkip,
  onNoMatch,
  isSkipping,
  isUnmatching,
}: {
  item: ReviewQueueItem;
  onResolve: () => void;
  onSkip: () => void;
  onNoMatch: () => void;
  isSkipping: boolean;
  isUnmatching: boolean;
}) {
  return (
    <li className="rounded-2xl border border-border bg-card p-4">
      <div className="mb-1 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-bold text-foreground">{item.raw_name}</p>
          <p className="text-sm text-muted-foreground">{item.normalized_name}</p>
        </div>
        <StatusBadge status={item.status} />
      </div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
          {item.source}
        </span>
        {item.event_title && (
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
            {item.event_title}
          </span>
        )}
        {item.candidates.length > 0 && (
          <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
            {item.candidates.length} candidate{item.candidates.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onResolve}
          className="flex min-h-[36px] items-center rounded-xl bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring"
        >
          Resolve
        </button>
        <button
          type="button"
          onClick={onSkip}
          disabled={isSkipping}
          className="flex min-h-[36px] items-center rounded-xl border border-border bg-background px-3 py-1.5 text-sm font-semibold text-foreground hover:bg-muted disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
        >
          {isSkipping ? 'Skipping…' : 'Skip'}
        </button>
        <button
          type="button"
          onClick={onNoMatch}
          disabled={isUnmatching}
          className="flex min-h-[36px] items-center rounded-xl border border-red-200 px-3 py-1.5 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
        >
          {isUnmatching ? 'Updating…' : 'No Match'}
        </button>
      </div>
    </li>
  );
}

export function NameMatchReviewPage() {
  const queryClient = useQueryClient();

  const [filters, setFilters] = useState<ReviewQueueFilters>({
    status: 'pending',
    source: '',
    page: 1,
    page_size: PAGE_SIZE,
  });
  const [pendingFilters, setPendingFilters] = useState({ status: 'pending', source: '' });
  const [resolveItem, setResolveItem] = useState<ReviewQueueItem | null>(null);
  const [actionItemId, setActionItemId] = useState<number | null>(null);
  const [actionType, setActionType] = useState<'skip' | 'unmatch' | null>(null);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['review-queue', filters],
    queryFn: () => getReviewQueue(filters),
  });

  const skipMutation = useMutation({
    mutationFn: (id: number) => skipQueueItem(id),
    onSuccess: () => {
      toast.success('Item skipped');
      queryClient.invalidateQueries({ queryKey: ['review-queue'] });
    },
    onError: (err: unknown) => {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Failed to skip item');
    },
    onSettled: () => {
      setActionItemId(null);
      setActionType(null);
    },
  });

  const unmatchMutation = useMutation({
    mutationFn: (id: number) => unmatchQueueItem(id),
    onSuccess: () => {
      toast.success('Marked as no match');
      queryClient.invalidateQueries({ queryKey: ['review-queue'] });
    },
    onError: (err: unknown) => {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Failed to update item');
    },
    onSettled: () => {
      setActionItemId(null);
      setActionType(null);
    },
  });

  const handleSearch = () => {
    setFilters((prev) => ({
      ...prev,
      status: pendingFilters.status || undefined,
      source: pendingFilters.source || undefined,
      page: 1,
    }));
  };

  const handleSkip = (id: number) => {
    setActionItemId(id);
    setActionType('skip');
    skipMutation.mutate(id);
  };

  const handleNoMatch = (id: number) => {
    setActionItemId(id);
    setActionType('unmatch');
    unmatchMutation.mutate(id);
  };

  const handleResolved = () => {
    setResolveItem(null);
    queryClient.invalidateQueries({ queryKey: ['review-queue'] });
  };

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1;
  const currentPage = filters.page ?? 1;

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="mx-auto flex max-w-2xl items-center justify-between">
          <h1 className="text-lg font-bold text-foreground">Name Review Queue</h1>
          <button
            type="button"
            onClick={() => refetch()}
            aria-label="Refresh"
            className="rounded-xl border border-border p-2 text-foreground/60 hover:bg-muted hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <RefreshCw size={16} aria-hidden="true" />
          </button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-2xl flex-1 space-y-4 p-4 pb-24">
        {/* Filter bar */}
        <section aria-label="Filters" className="rounded-2xl border border-border bg-card p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1">
              <label htmlFor="status-filter" className="mb-1 block text-xs font-semibold text-muted-foreground">
                Status
              </label>
              <select
                id="status-filter"
                value={pendingFilters.status}
                onChange={(e) => setPendingFilters((prev) => ({ ...prev, status: e.target.value }))}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              >
                {STATUS_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            <div className="flex-1">
              <label htmlFor="source-filter" className="mb-1 block text-xs font-semibold text-muted-foreground">
                Source
              </label>
              <input
                id="source-filter"
                type="text"
                value={pendingFilters.source}
                onChange={(e) => setPendingFilters((prev) => ({ ...prev, source: e.target.value }))}
                placeholder="Filter by source…"
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              />
            </div>
            <button
              type="button"
              onClick={handleSearch}
              className="flex min-h-[40px] items-center rounded-xl bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              Search
            </button>
          </div>
        </section>

        {/* Summary */}
        {data && (
          <p className="text-sm text-muted-foreground">
            {data.total} item{data.total !== 1 ? 's' : ''} found
          </p>
        )}

        {/* Content */}
        {isLoading && <LoadingSkeleton />}

        {isError && (
          <div className="rounded-2xl border border-border bg-card p-6 text-center" role="alert">
            <p className="mb-3 text-foreground">Failed to load review queue.</p>
            <button
              type="button"
              onClick={() => refetch()}
              className="rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              Retry
            </button>
          </div>
        )}

        {!isLoading && !isError && data?.items.length === 0 && (
          <div className="rounded-2xl border border-border bg-card p-8 text-center" aria-live="polite">
            <p className="text-base font-semibold text-foreground">No items in review queue</p>
            <p className="mt-1 text-sm text-muted-foreground">All names have been processed or no results match your filters.</p>
          </div>
        )}

        {!isLoading && !isError && data && data.items.length > 0 && (
          <ul className="space-y-3" aria-label="Review queue items">
            {data.items.map((item) => (
              <QueueCard
                key={item.id}
                item={item}
                onResolve={() => setResolveItem(item)}
                onSkip={() => handleSkip(item.id)}
                onNoMatch={() => handleNoMatch(item.id)}
                isSkipping={actionItemId === item.id && actionType === 'skip' && skipMutation.isPending}
                isUnmatching={actionItemId === item.id && actionType === 'unmatch' && unmatchMutation.isPending}
              />
            ))}
          </ul>
        )}

        {/* Pagination */}
        {data && data.total > PAGE_SIZE && (
          <nav aria-label="Pagination" className="flex items-center justify-between pt-2">
            <button
              type="button"
              onClick={() => setFilters((prev) => ({ ...prev, page: Math.max(1, (prev.page ?? 1) - 1) }))}
              disabled={currentPage <= 1}
              aria-label="Previous page"
              className="flex items-center gap-1 rounded-xl border border-border px-4 py-2 text-sm font-semibold text-foreground hover:bg-muted disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <ChevronLeft size={16} aria-hidden="true" /> Prev
            </button>
            <span className="text-sm text-muted-foreground">
              Page {currentPage} of {totalPages}
            </span>
            <button
              type="button"
              onClick={() => setFilters((prev) => ({ ...prev, page: Math.min(totalPages, (prev.page ?? 1) + 1) }))}
              disabled={currentPage >= totalPages}
              aria-label="Next page"
              className="flex items-center gap-1 rounded-xl border border-border px-4 py-2 text-sm font-semibold text-foreground hover:bg-muted disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              Next <ChevronRight size={16} aria-hidden="true" />
            </button>
          </nav>
        )}
      </main>

      {/* Resolve modal */}
      {resolveItem && (
        <ResolveMatchModal
          item={resolveItem}
          open={!!resolveItem}
          onClose={() => setResolveItem(null)}
          onResolved={handleResolved}
        />
      )}
    </div>
  );
}
