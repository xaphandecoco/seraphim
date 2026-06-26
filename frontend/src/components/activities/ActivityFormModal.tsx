import { useState, useEffect } from 'react';
import { X } from 'lucide-react';
import { toast } from 'sonner';
import type { ActivityDetail, ActivityCreate, ActivityUpdate, ActivityPriority } from '@/types/activity';
import { useActivityMeta, useAssignees, useCreateActivity, useUpdateActivity } from '@/hooks/useActivities';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Convert an ISO datetime string to datetime-local input value (YYYY-MM-DDTHH:mm). */
function toInputDt(iso: string | null | undefined): string {
  if (!iso) return '';
  return iso.slice(0, 16);
}

const TYPE_LABELS: Record<string, string> = {
  call: 'Call',
  visit: 'Visit',
  follow_up: 'Follow-up',
  meeting: 'Meeting',
  email: 'Email',
  note: 'Note',
  other: 'Other',
};

const PRIORITY_LABELS: Record<string, string> = {
  low: 'Low',
  normal: 'Normal',
  high: 'High',
  urgent: 'Urgent',
};

// ---------------------------------------------------------------------------
// Form state
// ---------------------------------------------------------------------------

interface FormState {
  activity_type: string;
  subject: string;
  details: string;
  activity_date: string;
  due_date: string;
  priority: string;
  assignee_user_id: string;
  target_contact_id: string;
}

function buildInitialForm(
  activity?: ActivityDetail,
  targetContactId?: number,
): FormState {
  if (activity) {
    return {
      activity_type: activity.activity_type,
      subject: activity.subject,
      details: activity.details ?? '',
      activity_date: toInputDt(activity.activity_date),
      due_date: toInputDt(activity.due_date),
      priority: activity.priority,
      assignee_user_id: activity.assignee_user_id?.toString() ?? '',
      target_contact_id: activity.target_contact_id?.toString() ?? '',
    };
  }
  return {
    activity_type: '',
    subject: '',
    details: '',
    activity_date: toInputDt(new Date().toISOString()),
    due_date: '',
    priority: 'normal',
    assignee_user_id: '',
    target_contact_id: targetContactId?.toString() ?? '',
  };
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ActivityFormModalProps {
  /** Populated in edit mode; undefined means create mode. */
  activity?: ActivityDetail;
  /** Pre-fills and locks target contact when launched from a contact profile. */
  targetContactId?: number;
  /** Display name shown next to the locked contact field. */
  targetContactName?: string;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ActivityFormModal({
  activity,
  targetContactId,
  targetContactName,
  onClose,
}: ActivityFormModalProps) {
  const isEdit = activity != null;

  const { data: meta } = useActivityMeta();
  const { data: assignees } = useAssignees();
  const createMutation = useCreateActivity();
  const updateMutation = useUpdateActivity();

  const [form, setForm] = useState<FormState>(() =>
    buildInitialForm(activity, targetContactId),
  );

  // Reset form whenever the modal is re-opened with a different activity.
  useEffect(() => {
    setForm(buildInitialForm(activity, targetContactId));
  }, [activity, targetContactId]);

  // Set default type once meta loads.
  useEffect(() => {
    if (meta?.types.length && !form.activity_type) {
      setForm((f) => ({ ...f, activity_type: meta.types[0] }));
    }
  }, [meta, form.activity_type]);

  const isPending = createMutation.isPending || updateMutation.isPending;

  const set = (field: keyof FormState) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>,
  ) => setForm((f) => ({ ...f, [field]: e.target.value }));

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.subject.trim()) {
      toast.error('Subject is required');
      return;
    }

    const contactId =
      targetContactId ??
      (form.target_contact_id ? Number(form.target_contact_id) : null);

    if (isEdit) {
      const body: ActivityUpdate = {
        activity_type: form.activity_type || undefined,
        subject: form.subject.trim(),
        details: form.details.trim() || null,
        activity_date: form.activity_date ? form.activity_date + ':00' : undefined,
        due_date: form.due_date ? form.due_date + ':00' : null,
        priority: (form.priority as ActivityPriority) || undefined,
      };
      updateMutation.mutate(
        { id: activity!.id, body },
        {
          onSuccess: () => {
            toast.success('Activity updated');
            onClose();
          },
          onError: (err: unknown) => {
            const e = err as { response?: { data?: { detail?: string } } };
            toast.error(e.response?.data?.detail ?? 'Something went wrong');
          },
        },
      );
    } else {
      const body: ActivityCreate = {
        activity_type: form.activity_type,
        subject: form.subject.trim(),
        details: form.details.trim() || null,
        activity_date: form.activity_date ? form.activity_date + ':00' : undefined,
        due_date: form.due_date ? form.due_date + ':00' : null,
        priority: (form.priority as ActivityPriority) || 'normal',
        assignee_user_id: form.assignee_user_id ? Number(form.assignee_user_id) : null,
        target_contact_id: contactId,
      };
      createMutation.mutate(body, {
        onSuccess: () => {
          toast.success('Activity created');
          onClose();
        },
        onError: (err: unknown) => {
          const e = err as { response?: { data?: { detail?: string } } };
          toast.error(e.response?.data?.detail ?? 'Something went wrong');
        },
      });
    }
  };

  const isContactLocked = targetContactId != null || (isEdit && activity?.target_contact_id != null);
  const lockedName = targetContactName ?? activity?.target_contact_name ?? `Contact #${contactId(form, targetContactId)}`;

  return (
    /* Backdrop */
    <div className="fixed inset-0 z-50 flex items-end justify-center md:items-center">
      <div
        className="absolute inset-0 bg-foreground/30 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel — bottom sheet on mobile, centered on md+ */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={isEdit ? 'Edit activity' : 'New activity'}
        className="relative w-full max-w-lg rounded-t-2xl border border-border bg-card p-5 shadow-xl md:rounded-2xl"
      >
        {/* Header */}
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-bold text-foreground">
            {isEdit ? 'Edit Activity' : 'New Activity'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg p-1.5 text-foreground/40 hover:bg-background hover:text-foreground"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Type */}
          <div>
            <label htmlFor="act-type" className="mb-1 block text-xs font-semibold text-foreground/70">
              Type <span aria-hidden="true">*</span>
            </label>
            <select
              id="act-type"
              aria-label="Type"
              value={form.activity_type}
              onChange={set('activity_type')}
              required
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="">Select type…</option>
              {(meta?.types ?? []).map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABELS[t] ?? t}
                </option>
              ))}
            </select>
          </div>

          {/* Subject */}
          <div>
            <label htmlFor="act-subject" className="mb-1 block text-xs font-semibold text-foreground/70">
              Subject <span aria-hidden="true">*</span>
            </label>
            <input
              id="act-subject"
              type="text"
              value={form.subject}
              onChange={set('subject')}
              required
              placeholder="Brief title…"
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-foreground/30 focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>

          {/* Details */}
          <div>
            <label htmlFor="act-details" className="mb-1 block text-xs font-semibold text-foreground/70">
              Details
            </label>
            <textarea
              id="act-details"
              value={form.details}
              onChange={set('details')}
              rows={3}
              placeholder="Optional notes…"
              className="w-full resize-none rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-foreground/30 focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>

          {/* Dates row */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="act-date" className="mb-1 block text-xs font-semibold text-foreground/70">
                Activity Date
              </label>
              <input
                id="act-date"
                type="datetime-local"
                value={form.activity_date}
                onChange={set('activity_date')}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div>
              <label htmlFor="act-due" className="mb-1 block text-xs font-semibold text-foreground/70">
                Due Date
              </label>
              <input
                id="act-due"
                type="datetime-local"
                value={form.due_date}
                onChange={set('due_date')}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
          </div>

          {/* Priority */}
          <div>
            <label htmlFor="act-priority" className="mb-1 block text-xs font-semibold text-foreground/70">
              Priority
            </label>
            <select
              id="act-priority"
              value={form.priority}
              onChange={set('priority')}
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {(meta?.priorities ?? ['low', 'normal', 'high', 'urgent']).map((p) => (
                <option key={p} value={p}>
                  {PRIORITY_LABELS[p] ?? p}
                </option>
              ))}
            </select>
          </div>

          {/* Assignee — shown in create mode only (edit: use PATCH on the dedicated field) */}
          {!isEdit && (
            <div>
              <label htmlFor="act-assignee" className="mb-1 block text-xs font-semibold text-foreground/70">
                Assignee
              </label>
              <select
                id="act-assignee"
                aria-label="Assignee"
                value={form.assignee_user_id}
                onChange={set('assignee_user_id')}
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
          )}

          {/* Target contact */}
          <div>
            <label htmlFor="act-contact" className="mb-1 block text-xs font-semibold text-foreground/70">
              Contact
            </label>
            {isContactLocked ? (
              <p className="rounded-xl border border-border bg-muted px-3 py-2 text-sm text-foreground/70">
                {lockedName}
              </p>
            ) : (
              <input
                id="act-contact"
                type="number"
                min={1}
                value={form.target_contact_id}
                onChange={set('target_contact_id')}
                placeholder="Contact ID (optional)"
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-foreground/30 focus:outline-none focus:ring-2 focus:ring-ring"
              />
            )}
          </div>

          {/* Submit */}
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="flex h-10 flex-1 items-center justify-center rounded-xl border border-border bg-background text-sm font-semibold text-foreground transition-colors hover:bg-muted"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isPending}
              className="flex h-10 flex-1 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98] disabled:opacity-60"
            >
              {isPending ? 'Saving…' : isEdit ? 'Save Changes' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper (extracted to avoid repetition in lockedName fallback)
// ---------------------------------------------------------------------------
function contactId(form: FormState, targetContactId?: number): string {
  return (targetContactId ?? form.target_contact_id) ? String(targetContactId ?? form.target_contact_id) : '?';
}
