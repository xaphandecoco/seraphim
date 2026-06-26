import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft, Play } from 'lucide-react';
import { fetchJobRuns, triggerJob, REGISTERED_JOBS } from '@/services/settings';
import { Pagination } from '@/components/ui/Pagination';
import { LoadingState, ErrorState, EmptyState } from '@/components/ui/StateViews';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { JobRunRow } from '@/components/settings/JobRunRow';
import type { Tone } from '@/components/ui/StatusBadge';
import type { JobRun } from '@/types';

const PAGE_SIZE = 20;

function runStatusTone(status: JobRun['status']): Tone {
  switch (status) {
    case 'success':
    case 'completed':
      return 'active';
    case 'running':
      return 'warning';
    case 'failed':
      return 'error';
    default:
      return 'muted';
  }
}

export function JobRunsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [jobNameFilter, setJobNameFilter] = useState('');

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['settings', 'jobs', page, jobNameFilter],
    queryFn: () => fetchJobRuns(page, PAGE_SIZE, jobNameFilter || undefined),
    placeholderData: (prev) => prev,
  });

  const trigger = useMutation({
    mutationFn: (jobName: string) => triggerJob(jobName),
    onSuccess: (_result, jobName) => {
      toast.success(`Job "${jobName.replace(/_/g, ' ')}" started`);
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['settings', 'jobs'] });
        queryClient.invalidateQueries({ queryKey: ['settings', 'system-status'] });
      }, 2000);
    },
    onError: (err: any, jobName) => {
      const detail = err.response?.data?.detail;
      if (err.response?.status === 409) {
        toast.error(detail || `Job "${jobName}" is already running`);
      } else if (err.response?.status === 403) {
        toast.error('Not authorized to trigger jobs');
      } else {
        toast.error(detail || `Failed to trigger "${jobName}"`);
      }
    },
  });

  const handleFilterChange = (value: string) => {
    setJobNameFilter(value);
    setPage(1);
  };

  const selectCls =
    'h-10 rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30';

  return (
    <div className="flex h-screen flex-col pb-20">
      {/* Header */}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/settings')}
            aria-label="Back to settings"
            className="text-foreground/50 hover:text-foreground"
          >
            <ArrowLeft size={20} aria-hidden="true" />
          </button>
          <h1 className="text-lg font-bold text-foreground">Scheduled Jobs</h1>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {/* Trigger panel */}
        <div className="mb-4 space-y-1">
          <h2 className="px-1 text-xs font-bold uppercase tracking-wide text-foreground/50">
            Manual Trigger
          </h2>
          <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
            <p className="mb-3 text-xs text-foreground/50">
              Trigger a job immediately for testing. Running jobs are guarded against duplicate execution.
            </p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {REGISTERED_JOBS.map((jobName) => (
                <button
                  key={jobName}
                  onClick={() => trigger.mutate(jobName)}
                  disabled={trigger.isPending}
                  className="flex items-center justify-center gap-1.5 rounded-xl border border-border bg-background px-2 py-2 text-xs font-medium text-foreground transition-colors hover:bg-primary/10 disabled:opacity-40"
                >
                  <Play size={10} aria-hidden="true" />
                  {jobName.replace(/_/g, ' ')}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Filter row */}
        <div className="mb-3 flex items-center gap-3">
          <h2 className="shrink-0 text-xs font-bold uppercase tracking-wide text-foreground/50">
            Job History
          </h2>
          <select
            value={jobNameFilter}
            onChange={(e) => handleFilterChange(e.target.value)}
            className={selectCls}
            aria-label="Filter by job name"
          >
            <option value="">All jobs</option>
            {REGISTERED_JOBS.map((j) => (
              <option key={j} value={j}>
                {j.replace(/_/g, ' ')}
              </option>
            ))}
          </select>
        </div>

        {/* Table / loading / error / empty */}
        <div className="rounded-2xl border border-border bg-card shadow-sm overflow-hidden">
          {isLoading ? (
            <div className="p-4">
              <LoadingState message="Loading job history…" />
            </div>
          ) : isError ? (
            <div className="p-4">
              <ErrorState
                message="Unable to load job runs"
                onRetry={() => refetch()}
              />
            </div>
          ) : !data || data.items.length === 0 ? (
            <div className="p-4">
              <EmptyState
                title="No job runs yet"
                description="No scheduled jobs have run yet. Trigger one manually above to test."
              />
            </div>
          ) : (
            <>
              {/* Desktop table */}
              <div className="hidden md:block overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-border bg-background/50">
                      <th className="px-3 py-2 text-xs font-semibold text-foreground/50">Job</th>
                      <th className="px-3 py-2 text-xs font-semibold text-foreground/50">Started</th>
                      <th className="px-3 py-2 text-xs font-semibold text-foreground/50">Duration</th>
                      <th className="px-3 py-2 text-xs font-semibold text-foreground/50">Status</th>
                      <th className="px-3 py-2 text-xs font-semibold text-foreground/50">Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((run) => (
                      <JobRunRow key={run.id} run={run} />
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Mobile card list */}
              <div className="md:hidden divide-y divide-border">
                {data.items.map((run) => (
                  <div key={run.id} className="p-3 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-foreground">
                        {run.job_name.replace(/_/g, ' ')}
                      </span>
                      <StatusBadge label={run.status} tone={runStatusTone(run.status)} />
                    </div>
                    <p className="text-[10px] text-foreground/50">
                      {new Date(run.started_at).toLocaleString()}
                      {run.duration_ms !== null && (
                        <span>
                          {' '}
                          &middot;{' '}
                          {run.duration_ms < 1000
                            ? `${run.duration_ms}ms`
                            : `${(run.duration_ms / 1000).toFixed(1)}s`}
                        </span>
                      )}
                    </p>
                    {run.detail && (
                      <p className="text-[10px] text-foreground/60 break-words">
                        {run.detail.length > 120
                          ? `${run.detail.slice(0, 120)}…`
                          : run.detail}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Pagination */}
          {data && data.total > 0 && (
            <div className="border-t border-border px-2 py-1">
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                total={data.total}
                onPageChange={setPage}
              />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
