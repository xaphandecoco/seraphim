import { useQuery } from '@tanstack/react-query';
import { fetchSystemStatus } from '@/services/settings';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { LoadingState, ErrorState } from '@/components/ui/StateViews';
import type { Tone } from '@/components/ui/StatusBadge';

function overallTone(overall: string): Tone {
  if (overall === 'ok') return 'active';
  if (overall === 'degraded') return 'warning';
  return 'error';
}

function overallLabel(overall: string): string {
  if (overall === 'ok') return 'All systems operational';
  if (overall === 'degraded') return 'Degraded';
  return 'Down';
}

function jobStatusTone(status: string | null): Tone {
  if (status === 'success' || status === 'completed') return 'active';
  if (status === 'running') return 'warning';
  if (status === 'failed') return 'error';
  return 'muted';
}

export function SystemStatusPanel() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['settings', 'system-status'],
    queryFn: fetchSystemStatus,
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return <LoadingState message="Checking system status…" />;
  }

  if (isError || !data) {
    return (
      <ErrorState
        message="Unable to load system status"
        onRetry={() => refetch()}
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Overall badge */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-foreground">Overall</span>
        <StatusBadge
          label={overallLabel(data.overall)}
          tone={overallTone(data.overall)}
        />
      </div>

      {/* Service rows */}
      <div className="space-y-2">
        {[
          { name: 'PostgreSQL', ok: data.services.postgres },
          { name: 'Redis', ok: data.services.redis },
          { name: 'CompreFace', ok: data.services.compreface },
        ].map((svc) => (
          <div key={svc.name} className="flex items-center justify-between text-sm">
            <span className="text-foreground/60">{svc.name}</span>
            <div className="flex items-center gap-2">
              <span
                className={`h-2.5 w-2.5 rounded-full ${svc.ok ? 'bg-green-500' : 'bg-red-500'}`}
                aria-label={svc.ok ? 'online' : 'offline'}
              />
              <span className={`text-xs font-medium ${svc.ok ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}>
                {svc.ok ? 'OK' : 'Error'}
              </span>
            </div>
          </div>
        ))}

        <div className="flex items-center justify-between text-sm">
          <span className="text-foreground/60">Queue depth</span>
          <span className="font-mono font-semibold text-foreground">{data.queue_depth}</span>
        </div>

        <div className="flex items-center justify-between text-sm">
          <span className="text-foreground/60">Safe mode</span>
          <StatusBadge
            label={data.safe_mode ? 'Enabled' : 'Off'}
            tone={data.safe_mode ? 'warning' : 'active'}
          />
        </div>

        {data.active_event_id !== null && (
          <div className="flex items-center justify-between text-sm">
            <span className="text-foreground/60">Active event</span>
            <span className="font-mono font-semibold text-foreground">#{data.active_event_id}</span>
          </div>
        )}
      </div>

      {/* Job health list */}
      {data.jobs.length > 0 && (
        <div className="border-t border-border pt-3">
          <p className="mb-2 text-xs font-semibold text-foreground/50">Scheduled Jobs</p>
          <div className="space-y-2">
            {data.jobs.map((job) => (
              <div key={job.job_name} className="flex items-center justify-between text-xs">
                <span className="truncate max-w-[200px] text-foreground/60">
                  {job.job_name.replace(/_/g, ' ')}
                </span>
                <div className="flex items-center gap-2">
                  <StatusBadge
                    label={job.last_status || 'never'}
                    tone={jobStatusTone(job.last_status)}
                  />
                  {job.last_run_at && (
                    <span className="text-foreground/40">
                      {new Date(job.last_run_at).toLocaleDateString()}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
