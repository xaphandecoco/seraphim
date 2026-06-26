import { useState } from 'react';
import { ListTodo, Plus } from 'lucide-react';
import { useAuthStore } from '@/store/authStore';
import { useActivitiesByContact, useUpdateActivity, useDeleteActivity } from '@/hooks/useActivities';
import { ActivityCard } from '@/components/activities/ActivityCard';
import { ActivityFormModal } from '@/components/activities/ActivityFormModal';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { LoadingState, EmptyState, ErrorState } from '@/components/ui/StateViews';
import { toast } from 'sonner';
import type { ActivityDetail, ActivityStatus } from '@/types/activity';

interface ActivitiesPanelProps {
  contactId: number;
}

export function ActivitiesPanel({ contactId }: ActivitiesPanelProps) {
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const isViewer = useAuthStore((s) => s.isViewer);
  const isVolunteer = useAuthStore((s) => s.isVolunteer);

  const { data, isLoading, isError, refetch } = useActivitiesByContact(contactId);

  const updateActivity = useUpdateActivity();
  const deleteActivity = useDeleteActivity();

  const [formModal, setFormModal] = useState<{
    open: boolean;
    activity?: ActivityDetail;
  }>({ open: false });

  const [deleteConfirm, setDeleteConfirm] = useState<{
    open: boolean;
    activity?: ActivityDetail;
  }>({ open: false });

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

  const handleDelete = () => {
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

  if (isLoading) {
    return <LoadingState message="Loading activities…" />;
  }

  if (isError) {
    return (
      <ErrorState
        message="Failed to load activities"
        onRetry={() => refetch()}
      />
    );
  }

  const items = data?.items ?? [];

  return (
    <div>
      {/* Panel header */}
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-bold text-foreground">Activities</h3>
        {/* New Task button — volunteer+ only */}
        {isVolunteer && (
          <button
            type="button"
            onClick={() => setFormModal({ open: true })}
            className="flex items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98]"
            aria-label="New Task for this contact"
          >
            <Plus size={13} aria-hidden="true" />
            New Task
          </button>
        )}
      </div>

      {/* Empty state */}
      {items.length === 0 ? (
        <EmptyState
          icon={ListTodo}
          title="No tasks for this contact yet"
          description={isViewer ? undefined : 'Create the first task using the button above.'}
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
              onStatusChange={handleStatusChange}
            />
          ))}
        </div>
      )}

      {/* Create / Edit modal */}
      {formModal.open && (
        <ActivityFormModal
          activity={formModal.activity}
          targetContactId={formModal.activity ? undefined : contactId}
          onClose={() => setFormModal({ open: false })}
        />
      )}

      {/* Delete confirm dialog */}
      {deleteConfirm.open && deleteConfirm.activity && (
        <ConfirmDialog
          message={`Delete activity "${deleteConfirm.activity.subject}"? This cannot be undone.`}
          confirmLabel="Delete"
          destructive
          onConfirm={handleDelete}
          onCancel={() => setDeleteConfirm({ open: false })}
        />
      )}
    </div>
  );
}
