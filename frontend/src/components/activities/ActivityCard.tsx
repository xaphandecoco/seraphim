import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Clock,
  MoreVertical,
  Edit,
  Trash2,
  UserCog,
  CheckCircle,
  Play,
  XCircle,
} from 'lucide-react';
import type { ActivityDetail, ActivityStatus } from '@/types/activity';

// ---------------------------------------------------------------------------
// Display maps
// ---------------------------------------------------------------------------

const PRIORITY_DOT: Record<string, string> = {
  low: 'bg-gray-400',
  normal: 'bg-blue-500',
  high: 'bg-amber-500',
  urgent: 'bg-red-500',
};

const STATUS_CHIP: Record<string, string> = {
  scheduled: 'bg-blue-100 text-blue-700',
  in_progress: 'bg-amber-100 text-amber-700',
  completed: 'bg-green-100 text-green-700',
  cancelled: 'bg-gray-100 text-gray-500',
};

const STATUS_LABELS: Record<string, string> = {
  scheduled: 'Scheduled',
  in_progress: 'In Progress',
  completed: 'Completed',
  cancelled: 'Cancelled',
};

const TYPE_LABELS: Record<string, string> = {
  call: 'Call',
  visit: 'Visit',
  follow_up: 'Follow-up',
  meeting: 'Meeting',
  email: 'Email',
  note: 'Note',
  other: 'Other',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isOverdue(activity: ActivityDetail): boolean {
  if (!activity.due_date) return false;
  if (activity.status === 'completed' || activity.status === 'cancelled') return false;
  return new Date(activity.due_date) < new Date();
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ActivityCardProps {
  activity: ActivityDetail;
  /** True when the viewer has admin role. Controls Reassign/Delete visibility. */
  isAdmin: boolean;
  /** True when the viewer has viewer role. Hides ALL mutation controls. */
  isViewer: boolean;
  onEdit?: (activity: ActivityDetail) => void;
  onDelete?: (activity: ActivityDetail) => void;
  onReassign?: (activity: ActivityDetail) => void;
  onStatusChange?: (activity: ActivityDetail, status: ActivityStatus) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ActivityCard({
  activity,
  isAdmin,
  isViewer,
  onEdit,
  onDelete,
  onReassign,
  onStatusChange,
}: ActivityCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const overdue = isOverdue(activity);
  const canMutate = !isViewer;
  const canStart = canMutate && activity.status === 'scheduled';
  const canComplete =
    canMutate &&
    (activity.status === 'scheduled' || activity.status === 'in_progress');
  const canCancel =
    canMutate &&
    activity.status !== 'cancelled' &&
    activity.status !== 'completed';

  return (
    <article className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <div className="flex items-start gap-3">
        {/* Priority dot */}
        <span
          className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${PRIORITY_DOT[activity.priority] ?? 'bg-gray-400'}`}
          aria-label={`Priority: ${activity.priority}`}
        />

        {/* Main content */}
        <div className="min-w-0 flex-1">
          {/* Subject + type badge */}
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-semibold text-foreground">
              {activity.subject}
            </p>
            <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-foreground/60">
              {TYPE_LABELS[activity.activity_type] ?? activity.activity_type}
            </span>
          </div>

          {/* Target contact link */}
          {activity.target_contact_id != null && activity.target_contact_name && (
            <Link
              to={`/contacts/${activity.target_contact_id}`}
              className="mt-0.5 block truncate text-xs text-primary hover:underline"
            >
              {activity.target_contact_name}
            </Link>
          )}

          {/* Assignee */}
          {activity.assignee_name && (
            <p className="mt-0.5 truncate text-xs text-foreground/50">
              {activity.assignee_name}
            </p>
          )}

          {/* Due date */}
          {activity.due_date && (
            <div
              className={`mt-1 flex items-center gap-1 text-xs ${overdue ? 'text-red-500' : 'text-foreground/50'}`}
            >
              {overdue && (
                <Clock
                  size={11}
                  aria-hidden="true"
                  data-testid="overdue-indicator"
                />
              )}
              <span>Due {new Date(activity.due_date).toLocaleDateString()}</span>
            </div>
          )}
        </div>

        {/* Right column: status chip + actions */}
        <div className="flex shrink-0 flex-col items-end gap-2">
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${STATUS_CHIP[activity.status] ?? 'bg-muted text-foreground/60'}`}
          >
            {STATUS_LABELS[activity.status] ?? activity.status}
          </span>

          {/* Quick-action overflow menu — hidden for viewers */}
          {canMutate && (
            <div className="relative">
              <button
                type="button"
                onClick={() => setMenuOpen((v) => !v)}
                aria-label="Activity actions"
                aria-expanded={menuOpen}
                className="rounded-lg p-1 text-foreground/40 hover:bg-background hover:text-foreground"
              >
                <MoreVertical size={15} aria-hidden="true" />
              </button>

              {menuOpen && (
                <div
                  className="absolute right-0 top-8 z-20 min-w-[148px] overflow-hidden rounded-xl border border-border bg-card shadow-xl"
                  onMouseLeave={() => setMenuOpen(false)}
                >
                  {/* Edit — all volunteers */}
                  <button
                    type="button"
                    onClick={() => {
                      onEdit?.(activity);
                      setMenuOpen(false);
                    }}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-foreground hover:bg-background"
                  >
                    <Edit size={12} aria-hidden="true" /> Edit
                  </button>

                  {/* Start */}
                  {canStart && (
                    <button
                      type="button"
                      onClick={() => {
                        onStatusChange?.(activity, 'in_progress');
                        setMenuOpen(false);
                      }}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-foreground hover:bg-background"
                    >
                      <Play size={12} aria-hidden="true" /> Start
                    </button>
                  )}

                  {/* Complete */}
                  {canComplete && (
                    <button
                      type="button"
                      onClick={() => {
                        onStatusChange?.(activity, 'completed');
                        setMenuOpen(false);
                      }}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-foreground hover:bg-background"
                    >
                      <CheckCircle size={12} aria-hidden="true" /> Complete
                    </button>
                  )}

                  {/* Cancel */}
                  {canCancel && (
                    <button
                      type="button"
                      onClick={() => {
                        onStatusChange?.(activity, 'cancelled');
                        setMenuOpen(false);
                      }}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-foreground hover:bg-background"
                    >
                      <XCircle size={12} aria-hidden="true" /> Cancel
                    </button>
                  )}

                  {/* Reassign — admin only */}
                  {isAdmin && (
                    <button
                      type="button"
                      onClick={() => {
                        onReassign?.(activity);
                        setMenuOpen(false);
                      }}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-foreground hover:bg-background"
                    >
                      <UserCog size={12} aria-hidden="true" /> Reassign
                    </button>
                  )}

                  {/* Delete — admin only */}
                  {isAdmin && (
                    <button
                      type="button"
                      onClick={() => {
                        onDelete?.(activity);
                        setMenuOpen(false);
                      }}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-red-500 hover:bg-background"
                    >
                      <Trash2 size={12} aria-hidden="true" /> Delete
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
