import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { DataTable } from '@/components/ui/DataTable';
import type { Column } from '@/components/ui/DataTable';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { ContactPickerModal } from '@/components/tasks/ContactPickerModal';
import { getOrphans, relinkOrphan, retireOrphan } from '@/services/frTransition';
import type { OrphanSubjectRow, Member } from '@/types';

export function FROrphanReviewPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [page, setPage] = useState(1);
  const pageSize = 50;

  // Row targeted by the current modal action
  const [relinkTarget, setRelinkTarget] = useState<OrphanSubjectRow | null>(null);
  const [retireTarget, setRetireTarget] = useState<OrphanSubjectRow | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['fr-orphans', page],
    queryFn: () => getOrphans(page, pageSize),
  });

  const rows = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['fr-orphans'] });

  // ---- Relink ---------------------------------------------------------------

  async function handleRelink(member: Member) {
    if (!relinkTarget) return;
    setActionLoading(true);
    try {
      await relinkOrphan(relinkTarget.id, member.contact_id);
      toast.success(`Subject relinked to ${member.display_name ?? `${member.first_name} ${member.last_name}`.trim()}`);
      setRelinkTarget(null);
      invalidate();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || 'Relink failed');
    } finally {
      setActionLoading(false);
    }
  }

  // ---- Retire ---------------------------------------------------------------

  async function handleRetireConfirm() {
    if (!retireTarget) return;
    setActionLoading(true);
    try {
      await retireOrphan(retireTarget.id);
      toast.success(`Subject "${retireTarget.subject_name}" retired`);
      setRetireTarget(null);
      invalidate();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || 'Retire failed');
    } finally {
      setActionLoading(false);
    }
  }

  // ---- Columns --------------------------------------------------------------

  const columns: Column<OrphanSubjectRow>[] = [
    {
      key: 'id',
      header: 'ID',
      className: 'w-16',
    },
    {
      key: 'subject_name',
      header: 'Subject Name',
    },
    {
      key: 'compreface_subject_id',
      header: 'CompreFace UUID',
      className: 'font-mono text-xs',
    },
    {
      key: 'enrollment_status',
      header: 'Status',
      render: (row) => (
        <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold bg-amber-100 text-amber-800">
          {row.enrollment_status}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      className: 'text-right',
      render: (row) => (
        <div className="flex items-center justify-end gap-2">
          <button
            onClick={(e) => { e.stopPropagation(); setRelinkTarget(row); }}
            disabled={actionLoading}
            className="rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground transition-colors hover:bg-primary/10 focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
          >
            Relink
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); setRetireTarget(row); }}
            disabled={actionLoading}
            className="rounded-lg border border-red-200 bg-background px-3 py-1.5 text-xs font-semibold text-red-600 transition-colors hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-300 disabled:opacity-50"
          >
            Retire
          </button>
        </div>
      ),
    },
  ];

  // ---- Render ---------------------------------------------------------------

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-5xl px-4 py-8">
        {/* Header */}
        <div className="mb-6 flex items-center gap-3">
          <button
            onClick={() => navigate('/settings')}
            aria-label="Back to Settings"
            className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-card text-foreground/60 transition-colors hover:bg-background hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <ArrowLeft size={16} aria-hidden="true" />
          </button>
          <div>
            <h1 className="text-xl font-bold text-foreground">Orphaned FR Subjects</h1>
            <p className="text-sm text-foreground/50">
              ComprefaceSubject rows with no linked contact. Relink to a contact or retire to remove from tracking.
            </p>
          </div>
        </div>

        {/* Count badge */}
        {!isLoading && (
          <p className="mb-4 text-sm text-foreground/60">
            {total === 0
              ? 'No orphaned subjects found.'
              : `${total} orphaned subject${total !== 1 ? 's' : ''}`}
          </p>
        )}

        {/* Table */}
        <div className="rounded-2xl border border-border bg-card overflow-hidden">
          <DataTable<OrphanSubjectRow>
            columns={columns}
            rows={rows}
            getRowKey={(r) => r.id}
            isLoading={isLoading}
            emptyState={
              <div className="py-16 text-center text-sm text-foreground/50">
                No orphaned subjects. The FR transition is fully linked.
              </div>
            }
          />
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-between">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="rounded-lg border border-border bg-card px-4 py-2 text-sm font-semibold text-foreground disabled:opacity-40 hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring"
            >
              Previous
            </button>
            <span className="text-sm text-foreground/60">
              Page {page} of {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="rounded-lg border border-border bg-card px-4 py-2 text-sm font-semibold text-foreground disabled:opacity-40 hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring"
            >
              Next
            </button>
          </div>
        )}
      </div>

      {/* Relink modal */}
      {relinkTarget && (
        <ContactPickerModal
          mode="edit"
          onSelect={handleRelink}
          onClose={() => setRelinkTarget(null)}
        />
      )}

      {/* Retire confirm dialog */}
      {retireTarget && (
        <ConfirmDialog
          message={`Retire subject "${retireTarget.subject_name}"? This sets enrollment_status to "purged" and cannot be undone without a manual DB edit.`}
          confirmLabel="Retire"
          destructive
          onConfirm={handleRetireConfirm}
          onCancel={() => setRetireTarget(null)}
        />
      )}
    </div>
  );
}
