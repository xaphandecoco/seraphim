import { useState } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft, Calendar, Edit, MapPin, Trash2, Loader2 } from 'lucide-react';

import { useAuthStore } from '@/store/authStore';
import { getEvent, deleteEvent } from '@/services/events';
import { submitNameList } from '@/services/attendance';
import type { NameListIntakeResponse } from '@/types/nameMatch';
import { EventTypeBadge } from '@/components/events/EventTypeBadge';
import { SessionTimeBadge } from '@/components/events/SessionTimeBadge';
import { EventFormDrawer } from '@/components/events/EventFormDrawer';
import { ParticipantGrid } from '@/components/events/ParticipantGrid';
import { BulkParticipantPanel } from '@/components/participants/BulkParticipantPanel';
import { ExportMenu } from '@/components/export/ExportMenu';
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
  const [selectedParticipantContactIds, setSelectedParticipantContactIds] = useState<number[]>([]);

  // Name list intake state
  const [nameListText, setNameListText] = useState('');
  const [nameListSource, setNameListSource] = useState<'manual' | 'community_report'>('manual');
  const [nameListResult, setNameListResult] = useState<NameListIntakeResponse | null>(null);

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

  // Name list intake mutation
  const nameListMutation = useMutation({
    mutationFn: () => {
      // Clear any prior result so a stale success banner never lingers during a re-submit.
      setNameListResult(null);
      const names = nameListText
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean);
      return submitNameList({ event_id: id!, names, source: nameListSource });
    },
    onSuccess: (result) => {
      setNameListResult(result);
      queryClient.invalidateQueries({ queryKey: ['participants', id] });
    },
    onError: (err: unknown) => {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Failed to submit');
    },
  });

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

        {/* Bulk participant panel + export */}
        <section aria-label="Bulk operations" className="space-y-2">
          <BulkParticipantPanel eventId={event.id} selectedIds={selectedParticipantContactIds} />
          <div className="flex justify-end">
            <ExportMenu jobType="participants" eventId={event.id} />
          </div>
        </section>

        {/* Participant grid */}
        <section aria-label="Participants section" className="rounded-2xl border border-border bg-card p-4">
          <ParticipantGrid
            eventId={event.id}
            canEdit={canEdit}
            selectedIds={selectedParticipantContactIds}
            onSelectionChange={setSelectedParticipantContactIds}
          />
        </section>

        {/* Attendance by Name List */}
        <section aria-label="Attendance by name list">
          <details className="rounded-2xl border border-border bg-card">
            <summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold text-foreground hover:bg-muted/50 focus:outline-none focus:ring-2 focus:ring-ring rounded-2xl">
              Attendance by Name List
            </summary>
            <div className="border-t border-border px-4 pb-4 pt-4 space-y-3">
              <div>
                <label htmlFor="name-list-textarea" className="mb-1 block text-xs font-semibold text-muted-foreground">
                  Names (one per line)
                </label>
                <textarea
                  id="name-list-textarea"
                  rows={6}
                  value={nameListText}
                  onChange={(e) => { setNameListText(e.target.value); setNameListResult(null); }}
                  placeholder="Enter names, one per line"
                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <div>
                <label htmlFor="name-list-source" className="mb-1 block text-xs font-semibold text-muted-foreground">Source</label>
                <select
                  id="name-list-source"
                  value={nameListSource}
                  onChange={(e) => setNameListSource(e.target.value as 'manual' | 'community_report')}
                  className="rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <option value="manual">Manual</option>
                  <option value="community_report">Community Report</option>
                </select>
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => nameListMutation.mutate()}
                  disabled={nameListMutation.isPending || !nameListText.trim()}
                  className="flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  {nameListMutation.isPending ? (
                    <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                  ) : null}
                  {nameListMutation.isPending ? 'Submitting…' : 'Submit'}
                </button>
              </div>
              {nameListResult && (
                <div className="rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700" aria-live="polite">
                  <p className="font-semibold">
                    {nameListResult.matched} matched, {nameListResult.review_queue} in review
                  </p>
                  {nameListResult.review_queue > 0 && (
                    <Link
                      to="/name-match/review"
                      className="mt-1 inline-block text-xs font-semibold text-primary hover:underline focus:outline-none"
                    >
                      Review unmatched names
                    </Link>
                  )}
                </div>
              )}
            </div>
          </details>
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
