import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { StatusBadge } from '@/components/ui/StatusBadge';
import type { Tone } from '@/components/ui/StatusBadge';
import type { JobRun } from '@/types';

interface JobRunRowProps {
  run: JobRun;
}

function statusTone(status: JobRun['status']): Tone {
  switch (status) {
    case 'success':
    case 'completed':
      return 'active';
    case 'running':
      return 'warning';
    case 'failed':
      return 'error';
    case 'skipped':
    case 'warning':
    default:
      return 'muted';
  }
}

function formatDuration(ms: number | null): string {
  if (ms === null) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function JobRunRow({ run }: JobRunRowProps) {
  const [expanded, setExpanded] = useState(false);

  const detail = run.detail ?? '';
  const isLong = detail.length > 80;
  const truncated = isLong ? detail.slice(0, 80) + '…' : detail;

  return (
    <tr className="border-b border-border last:border-0 hover:bg-background/50 transition-colors">
      <td className="px-3 py-2 text-xs font-medium text-foreground">
        {run.job_name.replace(/_/g, ' ')}
      </td>
      <td className="px-3 py-2 text-xs text-foreground/60 whitespace-nowrap">
        {new Date(run.started_at).toLocaleString()}
      </td>
      <td className="px-3 py-2 text-xs text-foreground/60 whitespace-nowrap">
        {formatDuration(run.duration_ms)}
      </td>
      <td className="px-3 py-2">
        <StatusBadge label={run.status} tone={statusTone(run.status)} />
      </td>
      <td className="px-3 py-2 text-xs text-foreground/60 max-w-[240px]">
        {detail ? (
          <div>
            <span>{expanded ? detail : truncated}</span>
            {isLong && (
              <button
                onClick={() => setExpanded((e) => !e)}
                className="ml-1 inline-flex items-center gap-0.5 text-primary hover:underline"
                aria-label={expanded ? 'Show less' : 'Show more'}
              >
                {expanded ? (
                  <ChevronUp size={12} aria-hidden="true" />
                ) : (
                  <ChevronDown size={12} aria-hidden="true" />
                )}
              </button>
            )}
          </div>
        ) : (
          <span className="text-foreground/30">—</span>
        )}
      </td>
    </tr>
  );
}
