import { useState } from 'react';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Users } from 'lucide-react';

import {
  listParticipants,
  updateParticipantStatus,
  addParticipant,
} from '@/services/events';
import { ContactPickerModal } from '@/components/tasks/ContactPickerModal';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { Pagination } from '@/components/ui/Pagination';
import { EmptyState } from '@/components/ui/StateViews';

import type { EventParticipant, Member } from '@/types';
import type { AxiosError } from 'axios';

// ---------- Constants ---------------------------------------------------------

const PARTICIPANT_STATUSES = [
  'registered',
  'attended',
  'absent',
  'cancelled',
] as const;

// ---------- Status dropdown cell ----------------------------------------------

interface StatusCellProps {
  participant: EventParticipant;
  eventId: number;
  canEdit: boolean;
}

function StatusCell({ participant, eventId, canEdit }: StatusCellProps) {
  const queryClient = useQueryClient();

  const mutation = useMutation<
    EventParticipant,
    AxiosError,
    string
  >({
    mutationFn: (status) =>
      updateParticipantStatus(eventId, participant.participant_id, { status }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['event-participants', eventId],
      });
    },
    onError: () => {
      toast.error('Failed to update status');
    },
  });

  if (!canEdit) {
    return (
      <span className="text-xs text-foreground/70 capitalize">
        {participant.status}
      </span>
    );
  }

  return (
    <select
      value={participant.status}
      onChange={(e) => mutation.mutate(e.target.value)}
      disabled={mutation.isPending}
      aria-label="Participant status"
      className="rounded-lg border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
    >
      {PARTICIPANT_STATUSES.map((s) => (
        <option key={s} value={s}>
          {s.charAt(0).toUpperCase() + s.slice(1)}
        </option>
      ))}
    </select>
  );
}

// ---------- Mobile card -------------------------------------------------------

interface ParticipantCardProps {
  participant: EventParticipant;
  eventId: number;
  canEdit: boolean;
}

function ParticipantCard({ participant, eventId, canEdit }: ParticipantCardProps) {
  const addedAt = participant.created_at
    ? new Date(participant.created_at).toLocaleDateString()
    : '—';

  return (
    <div className="rounded-2xl bg-card border border-border p-4 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold text-foreground truncate">
          {participant.contact_display_name ?? `Contact #${participant.contact_id}`}
        </span>
        <StatusCell
          participant={participant}
          eventId={eventId}
          canEdit={canEdit}
        />
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-foreground/50">
        <span>Source: {participant.source}</span>
        {participant.role && <span>Role: {participant.role}</span>}
        <span>Added: {addedAt}</span>
      </div>
    </div>
  );
}

// ---------- Props + query key ------------------------------------------------

export interface ParticipantGridProps {
  eventId: number;
  canEdit: boolean;
}

// ---------- Main component ----------------------------------------------------

export function ParticipantGrid({ eventId, canEdit }: ParticipantGridProps) {
  const queryClient = useQueryClient();

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [pickerOpen, setPickerOpen] = useState(false);

  const queryKey = [
    'event-participants',
    eventId,
    { page, pageSize, source: undefined, status: undefined },
  ] as const;

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () =>
      listParticipants(eventId, { page, page_size: pageSize }),
  });

  // ---------- Add participant mutation ----------------------------------------

  const addMutation = useMutation<
    EventParticipant,
    AxiosError,
    Member
  >({
    mutationFn: (member) =>
      addParticipant(eventId, {
        contact_id: member.contact_id,
        source: 'manual',
      }),
    onSuccess: () => {
      setPickerOpen(false);
      queryClient.invalidateQueries({
        queryKey: ['event-participants', eventId],
      });
      toast.success('Participant added');
    },
    onError: (err) => {
      const status = err.response?.status;
      if (status === 409) {
        toast.error('Contact is already registered for this event.');
      } else {
        toast.error('Failed to add participant');
      }
      setPickerOpen(false);
    },
  });

  const handleMemberSelect = (member: Member) => {
    addMutation.mutate(member);
  };

  // ---------- Desktop columns --------------------------------------------------

  const columns: Column<EventParticipant>[] = [
    {
      key: 'contact_display_name',
      header: 'Name',
      render: (row) => (
        <span className="font-medium">
          {row.contact_display_name ?? `Contact #${row.contact_id}`}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => (
        <StatusCell participant={row} eventId={eventId} canEdit={canEdit} />
      ),
    },
    {
      key: 'source',
      header: 'Source',
      render: (row) => (
        <span className="text-xs text-foreground/60">{row.source}</span>
      ),
    },
    {
      key: 'role',
      header: 'Role',
      render: (row) => (
        <span className="text-xs text-foreground/60">{row.role ?? '—'}</span>
      ),
    },
    {
      key: 'created_at',
      header: 'Added',
      render: (row) =>
        row.created_at
          ? new Date(row.created_at).toLocaleDateString()
          : '—',
    },
  ];

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  // ---------- Render ----------------------------------------------------------

  return (
    <section aria-label="Participants">
      {/* Section header */}
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-base font-bold text-foreground">
          <Users size={16} aria-hidden="true" />
          Participants
          {total > 0 && (
            <span className="ml-1 text-sm font-normal text-foreground/50">
              ({total})
            </span>
          )}
        </h2>

        {canEdit && (
          <button
            type="button"
            onClick={() => setPickerOpen(true)}
            className="flex min-h-[36px] items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
          >
            Add Participant
          </button>
        )}
      </div>

      {/* Desktop table */}
      <div className="hidden md:block">
        <DataTable
          columns={columns}
          rows={items}
          getRowKey={(row) => row.participant_id}
          isLoading={isLoading}
          emptyState={
            <EmptyState
              icon={Users}
              title="No participants yet"
              description="Add a participant to get started."
            />
          }
        />
      </div>

      {/* Mobile card stack */}
      <div className="block md:hidden space-y-2">
        {isLoading ? null : items.length === 0 ? (
          <EmptyState
            icon={Users}
            title="No participants yet"
            description="Add a participant to get started."
          />
        ) : (
          items.map((p) => (
            <ParticipantCard
              key={p.participant_id}
              participant={p}
              eventId={eventId}
              canEdit={canEdit}
            />
          ))
        )}
      </div>

      {/* Pagination */}
      {!isLoading && total > 0 && (
        <div className="mt-2 border-t border-border pt-2">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onPageChange={setPage}
            onPageSizeChange={(s) => {
              setPageSize(s);
              setPage(1);
            }}
          />
        </div>
      )}

      {/* Contact picker modal */}
      {pickerOpen && (
        <ContactPickerModal
          mode="add"
          onSelect={handleMemberSelect}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </section>
  );
}
