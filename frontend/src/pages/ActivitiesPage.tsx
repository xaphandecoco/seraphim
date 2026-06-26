import { useState } from 'react';
import { ListTodo, Plus, ChevronLeft, ChevronRight } from 'lucide-react';
import { toast } from 'sonner';
import { useAuthStore } from '@/store/authStore';
import {
  useActivities,
  useMyActivities,
  useUpdateActivity,
  useDeleteActivity,
  useReassignActivity,
  useAssignees,
} from '@/hooks/useActivities';
import { ActivityCard } from '@/components/activities/ActivityCard';
import { ActivityFormModal } from '@/components/activities/ActivityFormModal';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { LoadingState, EmptyState, ErrorState } from '@/components/ui/StateViews';
import type { ActivityDetail, ActivityFilters, ActivityStatus } from '@/types/activity';

type Tab = 'mine' | 'all';
type StatusFilter = 'open' | 'completed' | 'cancelled' | 'all';

const PAGE_SIZE = 20;

// ---------------------------------------------------------------------------
// Reassign mini-panel (shown as an overlay when admin clicks Reassign)
// ---------------------------------------------------------------------------

interface ReassignPanelProps {
  activity: ActivityDetail;
  onClose: () => void;
}

function ReassignPanel({ activity, onClose }: ReassignPanelProps) {
  const { data: assignees } = useAssignees();
  const reassign = useReassignActivity();
  const [newAssigneeId, setNewAssigneeId] = useState<string>(
    activity.assignee_user_id?.toString() ?? '',
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    reassign.mutate(
      {
        id: activity.id,
        assignee_user_id: newAssigneeId ? Number(newAssigneeId) : null,
      },
      {
        onSuccess: () => {
          toast.success('Activity reassigned');
          onClose();
        },
        onError: (err: unknown) => {
          const e = err as { response?: { data?: { detail?: string } } };
          toast.error(e.response?.data?.detail ?? 'Failed to reassign');
        },
      },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center md:items-center">
      <div
        className="absolute inset-0 bg-foreground/30 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Reassign activity"
        className="relative w-full max-w-sm rounded-t-2xl border border-border bg-card p-5 shadow-xl md:rounded-2xl"
      >
        <h2 className="mb-3 text-sm font-bold text-foreground">Reassign Activity</h2>
        <p className="mb-4 text-xs text-foreground/60 truncate">{activity.subject}</p>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="reassign-select" className="mb-1 block text-xs font-semibold text-foreground/70">
              Assign to
            </label>
            <select
              id="reassign-select"
              value={newAssigneeId}
              onChange={(e) => setNewAssigneeId(e.target.value)}
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="">Unassigned</option>
              {(assignees ?? []).map((a) => (
                <option key={a.id} value={a.id.toString()}>
                  {a.name ?? a.email}
                </option>
              ))}
            </select>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="flex h-10 flex-1 items-center justify-center rounded-xl border border-border bg-background text-sm font-semibold text-foreground hover:bg-muted"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={reassign.isPending}
              className="flex h-10 flex-1 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground hover:bg-primary/85 disabled:opacity-60"
            >
              {reassign.isPending ? 'Saving…' : 'Reassign'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function ActivitiesPage() {
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const isViewer = useAuthStore((s) => s.isViewer);
  const isVolunteer = useAuthStore((s) => s.isVolunteer);

  // ---- Tab / filter state ----
  const [tab, setTab] = useState<Tab>('mine');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('open');
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [priorityFilter, setPriorityFilter] = useState('');
  const [assigneeFilter, setAssigneeFilter] = useState('');
  const [page, setPage] = useState(1);

  // ---- Modal state ----
  const [formModal, setFormModal] = useState<{ open: boolean; activity?: ActivityDetail }>({
    open: false,
  });
  const [reassignModal, setReassignModal] = useState<{ open: boolean; activity?: ActivityDetail }>({
    open: false,
  });
  const [deleteConfirm, setDeleteConfirm] = useState<{ open: boolean; activity?: ActivityDetail }>({
    open: false,
  });

  // ---- Mutations ----
  const updateActivity = useUpdateActivity();
  const deleteActivity = useDeleteActivity();

  // ---- Build filters ----
  const statusParam =
    statusFilter === 'open'
      ? 'scheduled,in_progress'
      : statusFilter === 'all'
        ? undefined
        : statusFilter;

  const filters: ActivityFilters = {
    status: statusParam,
    priority: priorityFilter || undefined,
    overdue: overdueOnly || undefined,
    assignee_user_id: assigneeFilter ? Number(assigneeFilter) : undefined,
    page,
    page_size: PAGE_SIZE,
  };

  // ---- Queries (both always run; only display based on tab) ----
  const mineQuery = useMyActivities(filters);
  const allQuery = useActivities(filters);

  // For the assignee dropdown in filter bar
  const { data: assignees } = useAssignees();

  const activeQuery = tab === 'mine' ? mineQuery : allQuery;
  const { data, isLoading, isError, refetch } = activeQuery;
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // ---- Handlers ----
  const handleStatusChange = (activity: ActivityDetail, status: ActivityStatus) => {
    updateActivity.mutate(
      { id: activity.id, body: { status } },
      {
        onError: (err: unknown) => {
          const e = err as { response?: { data?: { detail?: string } } };
          toast.error(e.response?.data?.detail ?? 'Failed to update status');
        },
      },
    );
  };

  const handleDeleteConfirm = () => {
    if (!deleteConfirm.activity) return;
    deleteActivity.mutate(
      {
        id: deleteConfirm.activity.id,
        targetContactId: deleteConfirm.activity.target_contact_id,
      },
      {
        onSuccess: () => {
          toast.success('Activity deleted');
          setDeleteConfirm({ open: false });
        },
        onError: (err: unknown) => {
          const e = err as { response?: { data?: { detail?: string } } };
          toast.error(e.response?.data?.detail ?? 'Failed to delete activity');
        },
      },
    );
  };

  const resetPage = () => setPage(1);

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* Header */}
      <header className="sticky top-0 z-10 border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <h1 className="text-lg font-bold text-foreground">My Tasks</h1>
      </header>

      <main className="flex-1 px-4 py-4 pb-24">
        {/* Tab switcher */}
        <div className="mb-4 flex gap-1 rounded-2xl border border-border bg-card p-1">
          <button
            type="button"
            onClick={() => { setTab('mine'); resetPage(); }}
            className={`flex-1 rounded-xl py-1.5 text-xs font-semibold transition-colors ${
              tab === 'mine'
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-foreground/60 hover:text-foreground'
            }`}
          >
            My Tasks
          </button>
          <button
            type="button"
            onClick={() => { setTab('all'); resetPage(); }}
            className={`flex-1 rounded-xl py-1.5 text-xs font-semibold transition-colors ${
              tab === 'all'
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'text-foreground/60 hover:text-foreground'
            }`}
          >
            All
          </button>
        </div>

        {/* Filter bar */}
        <div className="mb-4 space-y-2">
          {/* Status chips */}
          <div className="flex flex-wrap gap-1.5">
            {(['open', 'completed', 'cancelled', 'all'] as StatusFilter[]).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => { setStatusFilter(s); resetPage(); }}
                className={`rounded-full px-3 py-1 text-[11px] font-semibold capitalize transition-colors ${
                  statusFilter === s
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted text-foreground/60 hover:bg-primary/10'
                }`}
              >
                {s === 'open' ? 'Open' : s.charAt(0).toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>

          {/* Secondary filters row */}
          <div className="flex flex-wrap items-center gap-2">
            {/* Priority filter */}
            <select
              value={priorityFilter}
              onChange={(e) => { setPriorityFilter(e.target.value); resetPage(); }}
              aria-label="Priority filter"
              className="rounded-xl border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="">All Priorities</option>
              <option value="low">Low</option>
              <option value="normal">Normal</option>
              <option value="high">High</option>
              <option value="urgent">Urgent</option>
            </select>

            {/* Overdue toggle */}
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-foreground/70">
              <input
                type="checkbox"
                checked={overdueOnly}
                onChange={(e) => { setOverdueOnly(e.target.checked); resetPage(); }}
                className="h-3.5 w-3.5 rounded border-border accent-primary"
              />
              Overdue only
            </label>

            {/* Assignee filter — admin/volunteer only */}
            {isVolunteer && (
              <select
                value={assigneeFilter}
                onChange={(e) => { setAssigneeFilter(e.target.value); resetPage(); }}
                aria-label="Assignee filter"
                className="rounded-xl border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="">All Assignees</option>
                {(assignees ?? []).map((a) => (
                  <option key={a.id} value={a.id.toString()}>
                    {a.name ?? a.email}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>

        {/* Content */}
        {isLoading ? (
          <LoadingState message="Loading activities…" />
        ) : isError ? (
          <ErrorState message="Failed to load activities" onRetry={() => refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={ListTodo}
            title={tab === 'mine' ? 'No tasks assigned to you' : 'No activities found'}
            description={
              isViewer
                ? undefined
                : 'Create a new task using the button below.'
            }
          />
        ) : (
          <div className="space-y-2">
            {items.map((activity) => (
              <ActivityCard
                key={activity.id}
                activity={activity}
                isAdmin={isAdmin}
                isViewer={isViewer}
                onEdit={(a) => setFormModal({ open: true, activity: a })}
                onDelete={(a) => setDeleteConfirm({ open: true, activity: a })}
                onReassign={(a) => setReassignModal({ open: true, activity: a })}
                onStatusChange={handleStatusChange}
              />
            ))}
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="mt-6 flex items-center justify-between text-xs text-foreground/60">
            <span>
              Page {page} of {totalPages} ({total} total)
            </span>
            <div className="flex gap-1">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                aria-label="Previous page"
                className="flex h-8 w-8 items-center justify-center rounded-xl border border-border bg-background disabled:opacity-40"
              >
                <ChevronLeft size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
                aria-label="Next page"
                className="flex h-8 w-8 items-center justify-center rounded-xl border border-border bg-background disabled:opacity-40"
              >
                <ChevronRight size={14} aria-hidden="true" />
              </button>
            </div>
          </div>
        )}
      </main>

      {/* FAB — volunteer+ only, hidden for viewer */}
      {isVolunteer && (
        <button
          type="button"
          onClick={() => setFormModal({ open: true })}
          aria-label="New Task"
          className="fixed bottom-20 right-4 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg hover:bg-primary/85 active:scale-95"
        >
          <Plus size={22} aria-hidden="true" />
        </button>
      )}

      {/* Create / Edit modal */}
      {formModal.open && (
        <ActivityFormModal
          activity={formModal.activity}
          onClose={() => setFormModal({ open: false })}
        />
      )}

      {/* Reassign modal */}
      {reassignModal.open && reassignModal.activity && (
        <ReassignPanel
          activity={reassignModal.activity}
          onClose={() => setReassignModal({ open: false })}
        />
      )}

      {/* Delete confirm */}
      {deleteConfirm.open && deleteConfirm.activity && (
        <ConfirmDialog
          message={`Delete "${deleteConfirm.activity.subject}"? This cannot be undone.`}
          confirmLabel="Delete"
          destructive
          onConfirm={handleDeleteConfirm}
          onCancel={() => setDeleteConfirm({ open: false })}
        />
      )}
    </div>
  );
}
