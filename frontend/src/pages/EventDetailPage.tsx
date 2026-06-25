import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft, Calendar, Edit, MapPin, Trash2 } from 'lucide-react';

import { useAuthStore } from '@/store/authStore';
import { getEvent, deleteEvent } from '@/services/events';
import { EventTypeBadge } from '@/components/events/EventTypeBadge';
import { SessionTimeBadge } from '@/components/events/SessionTimeBadge';
import { EventFormDrawer } from '@/components/events/EventFormDrawer';
import { ParticipantGrid } from '@/components/events/ParticipantGrid';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { ErrorState, LoadingState } from '@/components/ui/StateViews';

import type { AxiosError } from 'axios';

// ---------- Helpers -----------------------------------------------------------

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  });
}

// ---------- InfoRow helper ----------------------------------------------------

function InfoRow({
  label,
  value,
}: {
  label: string;
  value?: string | null;
}) {
  if (!value) return null;
  return (
    <div className="flex flex-col sm:flex-row sm:gap-4 py-1">
      <span className="w-36 shrink-0 text-xs font-medium text-foreground/60">
        {label}
      </span>
      <span className="text-sm text-foreground">{value}</span>
    </div>
  );
}

// ---------- Main page --------------------------------------------------------

export function EventDetailPage() {
  const { id: idParam } = useParams<{ id: string }>();
  const id = idParam ? Number(idParam) : undefined;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const isVolunteer = useAuthStore((s) => s.user?.role === 'volunteer');

  const [editOpen, setEditOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // ---------- Data fetch -------------------------------------------------------

  const {
    data: event,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['event', id],
    queryFn: () => getEvent(id!),
    enabled: !!id,
  });

  // ---------- Delete (soft) mutation ------------------------------------------

  const deleteMutation = useMutation<
    { id: number; is_active: boolean },
    AxiosError,
    number
  >({
    mutationFn: (eid) => deleteEvent(eid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['events'] });
      toast.success('Event deleted');
      navigate('/events');
    },
    onError: () => toast.error('Failed to delete event'),
  });

  const handleDeleteConfirm = () => {
    if (id) deleteMutation.mutate(id);
    setConfirmDelete(false);
  };

  const handleEditSaved = () => {
    queryClient.invalidateQueries({ queryKey: ['event', id] });
    queryClient.invalidateQueries({ queryKey: ['events'] });
    setEditOpen(false);
  };

  // ---------- Loading / error states -----------------------------------------

  const backButton = (
    <button
      type="button"
      onClick={() => navigate('/events')}
      aria-label="Back to Events"
      className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
    >
      <ArrowLeft size={18} aria-hidden="true" />
    </button>
  );

  if (isLoading) {
    return (
      <div className="flex h-screen flex-col">
        <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
          <div className="flex items-center gap-2">
            {backButton}
            <h1 className="text-lg font-bold text-foreground">Event</h1>
          </div>
        </header>
        <main className="flex-1 overflow-y-auto p-4">
          <LoadingState message="Loading event…" />
        </main>
      </div>
    );
  }

  if (isError || !event) {
    return (
      <div className="flex h-screen flex-col">
        <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
          <div className="flex items-center gap-2">
            {backButton}
            <h1 className="text-lg font-bold text-foreground">Event</h1>
          </div>
        </header>
        <main className="flex-1 overflow-y-auto p-4">
          <ErrorState
            message="Event not found"
            onRetry={() => refetch()}
          />
        </main>
      </div>
    );
  }

  // ---------- Render ----------------------------------------------------------

  const canEdit = isAdmin || isVolunteer;

  return (
    <div className="flex h-screen flex-col">
      {/* Page header */}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            {backButton}
            <h1 className="text-lg font-bold text-foreground truncate">
              {event.title}
            </h1>
          </div>

          {/* Admin actions */}
          {isAdmin && (
            <div className="flex shrink-0 items-center gap-2">
              <button
                type="button"
                onClick={() => setEditOpen(true)}
                aria-label="Edit event"
                className="flex min-h-[36px] items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-sm font-semibold text-foreground hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <Edit size={14} aria-hidden="true" />
                Edit
              </button>

              <button
                type="button"
                onClick={() => setConfirmDelete(true)}
                aria-label="Delete event"
                className="flex min-h-[36px] items-center gap-1.5 rounded-xl border border-red-200 bg-background px-3 py-1.5 text-sm font-semibold text-red-600 hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <Trash2 size={14} aria-hidden="true" />
                Delete
              </button>
            </div>
          )}
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-4 space-y-4 pb-24">
        {/* Event header card */}
        <section
          aria-label="Event overview"
          className="rounded-2xl border border-border bg-card p-4"
        >
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/20">
              <Calendar size={18} className="text-primary" aria-hidden="true" />
            </div>

            <div className="min-w-0 flex-1">
              <h2 className="text-xl font-bold text-foreground leading-tight">
                {event.title}
              </h2>

              {/* Badges */}
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                {event.event_type && (
                  <EventTypeBadge eventType={event.event_type} />
                )}
                {event.session_time && (
                  <SessionTimeBadge sessionTime={event.session_time} />
                )}
              </div>
            </div>
          </div>
        </section>

        {/* Scalar info card */}
        <section
          aria-label="Event details"
          className="rounded-2xl border border-border bg-card p-4"
        >
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-foreground/50">
            Details
          </h3>
          <div className="divide-y divide-border">
            <InfoRow
              label="Date"
              value={formatDate(event.occurrence_date)}
            />
            <InfoRow label="Starts" value={formatDateTime(event.start_at)} />
            <InfoRow label="Ends" value={formatDateTime(event.end_at)} />
            {event.location && (
              <div className="flex flex-col sm:flex-row sm:gap-4 py-1">
                <span className="w-36 shrink-0 text-xs font-medium text-foreground/60">
                  Location
                </span>
                <span className="flex items-center gap-1 text-sm text-foreground">
                  <MapPin size={13} aria-hidden="true" className="shrink-0" />
                  {event.location}
                </span>
              </div>
            )}
            <InfoRow
              label="Attendees"
              value={
                event.participant_counts
                  ? `${event.participant_counts.unique_count} unique · ${event.participant_counts.total_count} total`
                  : null
              }
            />
            <InfoRow
              label="Status"
              value={event.is_active === false ? 'Inactive' : 'Active'}
            />
            {event.external_id != null && (
              <InfoRow
                label="External ID"
                value={String(event.external_id)}
              />
            )}
          </div>
        </section>

        {/* Participant grid */}
        <section aria-label="Participants section" className="rounded-2xl border border-border bg-card p-4">
          <ParticipantGrid eventId={event.id} canEdit={canEdit} />
        </section>
      </main>

      {/* Edit drawer */}
      <EventFormDrawer
        open={editOpen}
        onClose={() => setEditOpen(false)}
        eventId={event.id}
        onSaved={handleEditSaved}
      />

      {/* Delete confirm dialog */}
      {confirmDelete && (
        <ConfirmDialog
          message={`Delete "${event.title}"? This will deactivate the event and cannot easily be undone.`}
          confirmLabel="Delete"
          destructive
          onConfirm={handleDeleteConfirm}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}
