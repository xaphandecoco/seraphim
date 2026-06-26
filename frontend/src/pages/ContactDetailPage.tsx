import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQueryClient, useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft, Edit, MoreVertical, Trash2, RotateCcw, User } from 'lucide-react';

import { useAuthStore } from '@/store/authStore';
import { useContact } from '@/hooks/useContact';
import { useContactAttendance } from '@/hooks/useContactAttendance';
import { useCustomFieldSchema } from '@/hooks/useCustomFieldSchema';
import { contactsApi } from '@/services/contacts';
import { FacePanel } from '@/components/contacts/FacePanel';
import { ConsentPanel } from '@/components/contacts/ConsentPanel';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { Pagination } from '@/components/ui/Pagination';
import { StatusBadge, getTierTone, getTierLabel } from '@/components/ui/StatusBadge';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/StateViews';

import type { ContactAttendanceItem, ContactReferenceChip } from '@/types';
import type { AxiosError } from 'axios';

// ---------- Attendance columns -----------------------------------------------

const attendanceCols: Column<ContactAttendanceItem>[] = [
  {
    key: 'event_title',
    header: 'Event',
    render: (row) => <span className="font-medium">{row.event_title}</span>,
  },
  {
    key: 'attended_at',
    header: 'Date',
    render: (row) =>
      row.attended_at
        ? new Date(row.attended_at).toLocaleDateString()
        : '—',
  },
  {
    key: 'source',
    header: 'Source',
    render: (row) => (
      <span className="text-xs text-foreground/50">{row.source ?? '—'}</span>
    ),
  },
];

// ---------- Info row helper --------------------------------------------------

function InfoRow({
  label,
  value,
  muted,
}: {
  label: string;
  value?: string | null;
  muted?: boolean;
}) {
  if (!value) return null;
  return (
    <div className="flex flex-col sm:flex-row sm:gap-4">
      <span
        className={`w-32 shrink-0 text-xs font-medium ${muted ? 'text-foreground/40' : 'text-foreground/60'}`}
      >
        {label}
      </span>
      <span className="text-sm text-foreground">{value}</span>
    </div>
  );
}

// ---------- Main page --------------------------------------------------------

type ConfirmAction = 'delete' | 'restore';

export function ContactDetailPage() {
  const { id: idParam } = useParams<{ id: string }>();
  const id = idParam ? Number(idParam) : undefined;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isAdmin = useAuthStore((s) => s.isAdmin);

  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);

  // Attendance pagination
  const [attPage, setAttPage] = useState(1);
  const [attPageSize] = useState(10);

  const {
    data: contact,
    isLoading,
    isError,
    refetch,
  } = useContact(id);

  const {
    data: attendance,
    isLoading: attLoading,
  } = useContactAttendance(id, { page: attPage, page_size: attPageSize });

  const { data: customFieldSchema } = useCustomFieldSchema('contact');

  const deleteMutation = useMutation<void, AxiosError, number>({
    mutationFn: (cid) => contactsApi.delete(cid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contacts'] });
      queryClient.invalidateQueries({ queryKey: ['contact', id] });
      toast.success('Contact deleted');
      navigate('/contacts');
    },
    onError: () => toast.error('Failed to delete contact'),
  });

  const restoreMutation = useMutation<unknown, AxiosError, number>({
    mutationFn: (cid) => contactsApi.restore(cid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contacts'] });
      queryClient.invalidateQueries({ queryKey: ['contact', id] });
      toast.success('Contact restored');
      setConfirmAction(null);
    },
    onError: () => toast.error('Failed to restore contact'),
  });

  const handleConfirm = () => {
    if (!id) return;
    if (confirmAction === 'delete') deleteMutation.mutate(id);
    if (confirmAction === 'restore') restoreMutation.mutate(id);
  };

  if (isLoading) {
    return (
      <div className="flex h-screen flex-col">
        <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => navigate('/contacts')}
              aria-label="Back to Contacts"
              className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <ArrowLeft size={18} aria-hidden="true" />
            </button>
            <h1 className="text-lg font-bold text-foreground">Contact</h1>
          </div>
        </header>
        <main className="flex-1 overflow-y-auto p-4">
          <LoadingState message="Loading contact…" />
        </main>
      </div>
    );
  }

  if (isError || !contact) {
    return (
      <div className="flex h-screen flex-col">
        <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => navigate('/contacts')}
              aria-label="Back to Contacts"
              className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <ArrowLeft size={18} aria-hidden="true" />
            </button>
            <h1 className="text-lg font-bold text-foreground">Contact</h1>
          </div>
        </header>
        <main className="flex-1 overflow-y-auto p-4">
          <ErrorState
            message="Failed to load contact"
            onRetry={() => refetch()}
          />
        </main>
      </div>
    );
  }

  const initials = [contact.first_name?.[0], contact.last_name?.[0]]
    .filter(Boolean)
    .join('')
    .toUpperCase();

  const faceEnrolled = contact.face?.enrolled ?? false;

  return (
    <div className="flex h-screen flex-col">
      {/* Page header */}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => navigate('/contacts')}
              aria-label="Back to Contacts"
              className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <ArrowLeft size={18} aria-hidden="true" />
            </button>
            <h1 className="text-lg font-bold text-foreground">Contact</h1>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => navigate(`/contacts/${id}/edit`)}
              className="flex min-h-[36px] items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-sm font-semibold text-foreground hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
              aria-label="Edit Contact"
            >
              <Edit size={14} aria-hidden="true" />
              Edit
            </button>

            {/* Overflow menu */}
            <div className="relative">
              <button
                type="button"
                onClick={() => setMenuOpen((v) => !v)}
                aria-label="More options"
                aria-expanded={menuOpen}
                className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <MoreVertical size={16} aria-hidden="true" />
              </button>

              {menuOpen && (
                <>
                  <div
                    className="fixed inset-0 z-10"
                    onClick={() => setMenuOpen(false)}
                    aria-hidden="true"
                  />
                  <div className="absolute right-0 z-20 mt-1 w-44 rounded-2xl border border-border bg-card shadow-lg">
                    <button
                      type="button"
                      onClick={() => {
                        setMenuOpen(false);
                        setConfirmAction('delete');
                      }}
                      className="flex w-full items-center gap-2 rounded-t-2xl px-4 py-3 text-sm font-medium text-red-600 hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-ring"
                    >
                      <Trash2 size={14} aria-hidden="true" />
                      Delete
                    </button>

                    {isAdmin && contact.is_deleted && (
                      <button
                        type="button"
                        onClick={() => {
                          setMenuOpen(false);
                          setConfirmAction('restore');
                        }}
                        className="flex w-full items-center gap-2 rounded-b-2xl px-4 py-3 text-sm font-medium text-foreground hover:bg-primary/10 focus:outline-none focus:ring-2 focus:ring-ring"
                      >
                        <RotateCcw size={14} aria-hidden="true" />
                        Restore
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-4 space-y-4 pb-24">
        {/* (a) Header card */}
        <section
          data-slot="header"
          className="bg-card rounded-2xl border border-border p-4"
        >
          <div className="flex items-start gap-4">
            {/* Avatar */}
            <div className="shrink-0">
              {contact.face?.thumb_url ? (
                <img
                  src={contact.face.thumb_url}
                  alt={contact.display_name}
                  className="h-16 w-16 rounded-2xl object-cover"
                />
              ) : (
                <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                  {initials ? (
                    <span className="text-xl font-bold">{initials}</span>
                  ) : (
                    <User size={24} aria-hidden="true" />
                  )}
                </div>
              )}
            </div>

            {/* Name + badges */}
            <div className="min-w-0 flex-1">
              <h2 className="text-xl font-bold text-foreground leading-tight">
                {contact.display_name}
              </h2>
              {contact.nickname && (
                <p className="text-sm text-foreground/50 mt-0.5">
                  "{contact.nickname}"
                </p>
              )}
              <div className="mt-2 flex flex-wrap gap-1.5">
                {contact.contact_type && (
                  <StatusBadge label={contact.contact_type} tone="muted" />
                )}
                {contact.contact_subtype && (
                  <StatusBadge label={contact.contact_subtype} tone="muted" />
                )}
                <StatusBadge
                  label={getTierLabel(contact.tier)}
                  tone={getTierTone(contact.tier)}
                />
                {contact.is_active != null && (
                  <StatusBadge
                    label={contact.is_active ? 'Active' : 'Inactive'}
                    tone={contact.is_active ? 'active' : 'muted'}
                  />
                )}
                {contact.is_regular != null && (
                  <StatusBadge
                    label={contact.is_regular ? 'Regular' : 'Non-regular'}
                    tone={contact.is_regular ? 'active' : 'muted'}
                  />
                )}
                {contact.is_connected != null && (
                  <StatusBadge
                    label={contact.is_connected ? 'Connected' : 'Not connected'}
                    tone={contact.is_connected ? 'warning' : 'muted'}
                  />
                )}
                {contact.is_deleted && (
                  <StatusBadge label="Deleted" tone="error" />
                )}
              </div>
            </div>
          </div>
        </section>

        {/* (b) Core info card */}
        <section
          data-slot="core-info"
          className="bg-card rounded-2xl border border-border p-4 space-y-2"
        >
          <h3 className="text-sm font-semibold text-foreground mb-3">Core Info</h3>
          <InfoRow label="Phone" value={contact.phone} />
          <InfoRow label="Email" value={contact.email} />
          <InfoRow label="Birth date" value={contact.birth_date} />
          <InfoRow label="Gender" value={contact.gender} />
          <InfoRow label="Suffix" value={contact.suffix} />
          <InfoRow label="Address" value={contact.street_address} />
          <InfoRow label="Legacy ID" value={contact.external_id} muted />
          {!contact.phone && !contact.email && !contact.birth_date && !contact.gender && !contact.suffix && !contact.street_address && !contact.external_id && (
            <p className="text-sm text-foreground/40">No core info recorded.</p>
          )}
        </section>

        {/* (c) Custom fields card — rendered per schema when custom_data is present */}
        {contact.custom_data && Object.keys(contact.custom_data).length > 0 && (() => {
          // Build a flat map of field name → field def from the schema
          const fieldDefMap = new Map(
            (customFieldSchema?.groups ?? []).flatMap((g) => g.fields.map((f) => [f.name, f]))
          );

          // Build a lookup map for resolved contact-reference chips
          const chipMap = new Map<number, ContactReferenceChip>(
            (contact.contact_reference_chips ?? []).map((c) => [c.id, c])
          );

          // Determine the ordered list of entries to render:
          // Prefer schema-ordered fields that have a value; fall back to raw entries.
          const entriesToRender: Array<{ key: string; label: string; dataType: string | null; val: unknown }> =
            fieldDefMap.size > 0
              ? Array.from(fieldDefMap.values())
                  .filter((f) => f.is_active && contact.custom_data![f.name] !== undefined)
                  .map((f) => ({
                    key: f.name,
                    label: f.label,
                    dataType: f.data_type,
                    val: contact.custom_data![f.name],
                  }))
              : Object.entries(contact.custom_data).map(([key, val]) => ({
                  key,
                  label: key,
                  dataType: null,
                  val,
                }));

          return (
            <section
              data-slot="custom-fields"
              className="bg-card rounded-2xl border border-border p-4"
            >
              <h3 className="text-sm font-semibold text-foreground mb-3">Custom Fields</h3>
              <div className="space-y-2">
                {entriesToRender.map(({ key, label, dataType, val }) => (
                  <div key={key} className="flex flex-col sm:flex-row sm:gap-4">
                    <span className="w-32 shrink-0 text-xs font-medium text-foreground/60">
                      {label}
                    </span>
                    <span className="text-sm text-foreground">
                      {dataType === 'contact_reference' ? (
                        // Render as resolved-name chips linking to /contacts/:refId
                        <span className="flex flex-wrap gap-1">
                          {(Array.isArray(val) ? (val as number[]) : [val as number]).map((refId) => {
                            const chip = chipMap.get(refId);
                            const chipLabel = chip ? chip.display_name : `Contact #${refId}`;
                            return (
                              <button
                                key={refId}
                                type="button"
                                onClick={() => navigate(`/contacts/${refId}`)}
                                className="inline-flex min-h-[44px] items-center rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary hover:bg-primary/20 focus:outline-none focus:ring-2 focus:ring-ring"
                              >
                                {chipLabel}
                              </button>
                            );
                          })}
                        </span>
                      ) : Array.isArray(val) ? (
                        // Non-contact_reference arrays: plain muted pills, no links
                        <span className="flex flex-wrap gap-1">
                          {(val as (string | number | boolean)[]).map((v, i) => (
                            <span
                              key={i}
                              className="rounded-full bg-muted px-2 py-0.5 text-xs text-foreground/70"
                            >
                              {String(v)}
                            </span>
                          ))}
                        </span>
                      ) : val === null || val === undefined ? (
                        <span className="text-foreground/40">—</span>
                      ) : (
                        String(val)
                      )}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          );
        })()}

        {/* (d) Face panel */}
        <section
          data-slot="face-panel"
          className="bg-card rounded-2xl border border-border p-4"
        >
          <h3 className="text-sm font-semibold text-foreground mb-3">Face Recognition</h3>
          {faceEnrolled ? (
            <FacePanel contactId={id!} readOnly />
          ) : (
            <>
              <FacePanel contactId={id!} readOnly />
              <EmptyState
                icon={User}
                title="No face enrolled"
                description="Upload a photo to enroll this contact"
              />
            </>
          )}
        </section>

        {/* (e) Attendance history */}
        <section
          data-slot="attendance-history"
          className="bg-card rounded-2xl border border-border p-4"
        >
          <h3 className="text-sm font-semibold text-foreground mb-3">Attendance History</h3>
          <DataTable<ContactAttendanceItem>
            columns={attendanceCols}
            rows={attendance?.items ?? []}
            getRowKey={(row) => `${row.event_id}-${row.attended_at}`}
            isLoading={attLoading}
            emptyState={
              <EmptyState
                title="No attendance recorded"
                description="This contact has no event attendance history"
              />
            }
          />
          {attendance && attendance.total > 0 && (
            <div className="mt-2 border-t border-border pt-2">
              <Pagination
                page={attPage}
                pageSize={attPageSize}
                total={attendance.total}
                onPageChange={setAttPage}
              />
            </div>
          )}
        </section>

        {/* (f) Consent panel — S08 ConsentPanel (replaces S24 static indicator) */}
        <ConsentPanel contactId={id!} />

        <section
          data-slot="activities"
          className="bg-card rounded-2xl border border-border p-4"
        >
          <p className="text-sm text-foreground/50">Activities — S12</p>
        </section>
      </main>

      {/* Confirm dialog */}
      {confirmAction && (
        <ConfirmDialog
          message={
            confirmAction === 'delete'
              ? `Delete "${contact.display_name}"? This action can be reversed by an admin.`
              : `Restore "${contact.display_name}"?`
          }
          confirmLabel={confirmAction === 'delete' ? 'Delete' : 'Restore'}
          destructive={confirmAction === 'delete'}
          onConfirm={handleConfirm}
          onCancel={() => setConfirmAction(null)}
        />
      )}
    </div>
  );
}
