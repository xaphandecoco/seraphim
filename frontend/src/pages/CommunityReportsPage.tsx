import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, Plus } from 'lucide-react';
import { listCommunityReports } from '@/services/communityReports';
import type { CommunityReportFilters } from '@/services/communityReports';
import type { CommunityReportStatus } from '@/types/nameMatch';

const PAGE_SIZE = 25;

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'All statuses' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'pending', label: 'Pending' },
  { value: 'processing', label: 'Processing' },
  { value: 'complete', label: 'Complete' },
  { value: 'partial', label: 'Partial' },
  { value: 'archived', label: 'Archived' },
];

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
    pending: 'bg-amber-50 text-amber-700',
    processing: 'bg-blue-50 text-blue-600',
    complete: 'bg-green-50 text-green-700',
    partial: 'bg-orange-50 text-orange-600',
  };
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${styles[matchStatus] ?? 'bg-muted text-muted-foreground'}`}>
      {matchStatus}
    </span>
  );
}

function LoadingSkeleton() {
  return (
    <ul className="space-y-3" aria-busy="true" aria-label="Loading">
      {Array.from({ length: 5 }).map((_, i) => (
        <li key={i} className="animate-pulse rounded-2xl border border-border bg-card p-4">
          <div className="mb-2 h-4 w-56 rounded bg-muted" />
          <div className="mb-3 h-3 w-40 rounded bg-muted" />
          <div className="h-3 w-24 rounded bg-muted" />
        </li>
      ))}
    </ul>
  );
}

export function CommunityReportsPage() {
  const [filters, setFilters] = useState<CommunityReportFilters>({
    page: 1,
    page_size: PAGE_SIZE,
  });
  const [pendingFilters, setPendingFilters] = useState({
    status: '',
    zone: '',
    date_from: '',
    date_to: '',
  });

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['community-reports', filters],
    queryFn: () => listCommunityReports(filters),
  });

  const handleSearch = () => {
    setFilters({
      status: pendingFilters.status || undefined,
      zone: pendingFilters.zone || undefined,
      date_from: pendingFilters.date_from || undefined,
      date_to: pendingFilters.date_to || undefined,
      page: 1,
      page_size: PAGE_SIZE,
    });
  };

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1;
  const currentPage = filters.page ?? 1;

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="mx-auto flex max-w-2xl items-center justify-between">
          <h1 className="text-lg font-bold text-foreground">Community Reports</h1>
          <Link
            to="/community-reports/new"
            className="flex items-center gap-1.5 rounded-xl bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <Plus size={16} aria-hidden="true" />
            New Report
          </Link>
        </div>
      </header>

      <main className="mx-auto w-full max-w-2xl flex-1 space-y-4 p-4 pb-24">
        {/* Filters */}
        <section aria-label="Filters" className="rounded-2xl border border-border bg-card p-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="status-filter" className="mb-1 block text-xs font-semibold text-muted-foreground">Status</label>
              <select
                id="status-filter"
                value={pendingFilters.status}
                onChange={(e) => setPendingFilters((p) => ({ ...p, status: e.target.value }))}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              >
                {STATUS_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="zone-filter" className="mb-1 block text-xs font-semibold text-muted-foreground">Zone</label>
              <input
                id="zone-filter"
                type="text"
                value={pendingFilters.zone}
                onChange={(e) => setPendingFilters((p) => ({ ...p, zone: e.target.value }))}
                placeholder="Filter by zone…"
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div>
              <label htmlFor="date-from" className="mb-1 block text-xs font-semibold text-muted-foreground">Date from</label>
              <input
                id="date-from"
                type="date"
                value={pendingFilters.date_from}
                onChange={(e) => setPendingFilters((p) => ({ ...p, date_from: e.target.value }))}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div>
              <label htmlFor="date-to" className="mb-1 block text-xs font-semibold text-muted-foreground">Date to</label>
              <input
                id="date-to"
                type="date"
                value={pendingFilters.date_to}
                onChange={(e) => setPendingFilters((p) => ({ ...p, date_to: e.target.value }))}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
          </div>
          <div className="mt-3 flex justify-end">
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
            {data.total} report{data.total !== 1 ? 's' : ''} found
          </p>
        )}

        {/* Content */}
        {isLoading && <LoadingSkeleton />}

        {isError && (
          <div className="rounded-2xl border border-border bg-card p-6 text-center" role="alert">
            <p className="mb-3 text-foreground">Failed to load community reports.</p>
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
            <p className="text-base font-semibold text-foreground">No community reports found</p>
            <p className="mt-1 text-sm text-muted-foreground">Submit a new report to get started.</p>
            <Link
              to="/community-reports/new"
              className="mt-4 inline-flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90"
            >
              <Plus size={14} aria-hidden="true" /> New Report
            </Link>
          </div>
        )}

        {!isLoading && !isError && data && data.items.length > 0 && (
          <ul className="space-y-3" aria-label="Community reports">
            {data.items.map((report) => (
              <li key={report.id}>
                <Link
                  to={`/community-reports/${report.id}`}
                  className="block rounded-2xl border border-border bg-card p-4 hover:border-primary/50 hover:bg-muted/50 focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2 mb-1">
                    <p className="font-bold text-foreground">
                      {report.event_title ?? 'No event'}
                    </p>
                    <StatusBadge status={report.status} />
                  </div>
                  <div className="flex flex-wrap gap-2 text-sm text-muted-foreground mb-2">
                    {report.zone && <span>{report.zone}</span>}
                    <span>{report.date_of_activity}</span>
                    {report.submitted_by_name && <span>by {report.submitted_by_name}</span>}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <MatchStatusBadge matchStatus={report.match_status} />
                    <span className="text-xs text-muted-foreground">
                      {report.matched_count} matched · {report.review_count} in review
                    </span>
                  </div>
                </Link>
              </li>
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
    </div>
  );
}
