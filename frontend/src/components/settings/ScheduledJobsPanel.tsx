import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Play, Clock } from 'lucide-react';
import { fetchSystemStatus, triggerJob, REGISTERED_JOBS } from '@/services/settings';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { LoadingState, ErrorState } from '@/components/ui/StateViews';
import type { Tone } from '@/components/ui/StatusBadge';

function jobStatusTone(status: string | null): Tone {
  if (status === 'success' || status === 'completed') return 'active';
  if (status === 'running') return 'warning';
  if (status === 'failed') return 'error';
  return 'muted';
}

export function ScheduledJobsPanel() {
  const queryClient = useQueryClient();

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['settings', 'system-status'],
    queryFn: fetchSystemStatus,
    refetchInterval: 30_000,
  });

  const trigger = useMutation({
    mutationFn: (jobName: string) => triggerJob(jobName),
    onSuccess: (_result, jobName) => {
      toast.success(`Job "${jobName.replace(/_/g, ' ')}" started`);
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['settings', 'system-status'] });
        queryClient.invalidateQueries({ queryKey: ['settings', 'jobs'] });
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

  const jobMap = Object.fromEntries(
    (data?.jobs ?? []).map((j) => [j.job_name, j]),
  );

  if (isLoading) {
    return <LoadingState message="Loading job status…" />;
  }

  if (isError) {
    return (
      <ErrorState
        message="Unable to load job status"
        onRetry={() => refetch()}
      />
    );
  }

  return (
    <div className="space-y-1">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xs font-bold uppercase tracking-wide text-foreground/50">
          Scheduled Jobs
        </h2>
        <Link
          to="/settings/jobs"
          className="text-xs font-medium text-primary hover:underline"
        >
          View full history →
        </Link>
      </div>

      <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
        <div className="space-y-3">
          {REGISTERED_JOBS.map((jobName) => {
              const job = jobMap[jobName];
              return (
                <div key={jobName} className="flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-medium text-foreground">
                      {jobName.replace(/_/g, ' ')}
                    </p>
                    {job?.last_run_at ? (
                      <p className="flex items-center gap-1 text-[10px] text-foreground/40">
                        <Clock size={10} aria-hidden="true" />
                        {new Date(job.last_run_at).toLocaleString()}
                      </p>
                    ) : (
                      <p className="text-[10px] text-foreground/30">Never run</p>
                    )}
                  </div>
                  <StatusBadge
                    label={job?.last_status || 'never'}
                    tone={jobStatusTone(job?.last_status ?? null)}
                  />
                  <button
                    onClick={() => trigger.mutate(jobName)}
                    disabled={trigger.isPending}
                    aria-label={`Trigger ${jobName}`}
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-background text-foreground/60 transition-colors hover:bg-primary/10 hover:text-primary disabled:opacity-40"
                  >
                    <Play size={11} aria-hidden="true" />
                  </button>
                </div>
              );
            })}
        </div>
      </div>
    </div>
  );
}
