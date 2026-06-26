/**
 * ProfilesPage — /profiles (AdminRoute)
 *
 * Lists all profiles with actions: Edit, Delete (via ConfirmDialog),
 * is_public inline toggle. Warns when no profile is public.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import type { AxiosError } from 'axios';
import { ClipboardList, Plus, AlertTriangle, Pencil } from 'lucide-react';

import { useProfiles, useDeleteProfile } from '@/hooks/useProfiles';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { Pagination } from '@/components/ui/Pagination';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { EmptyState, ErrorState } from '@/components/ui/StateViews';
import type { ProfileResponse } from '@/types/profile';

interface ApiError {
  detail?: string;
}

// ---------- Columns -----------------------------------------------------------

function ProfilesPageColumns({
  onEdit,
  onDelete,
  onTogglePublic,
}: {
  onEdit: (p: ProfileResponse) => void;
  onDelete: (p: ProfileResponse) => void;
  onTogglePublic: (p: ProfileResponse) => void;
}): Column<ProfileResponse>[] {
  return [
    {
      key: 'name',
      header: 'Name',
      render: (row) => (
        <span className="font-medium text-foreground">{row.name}</span>
      ),
    },
    {
      key: 'entity',
      header: 'Entity',
      render: (row) => (
        <StatusBadge label={row.entity} tone="muted" />
      ),
    },
    {
      key: 'is_public',
      header: 'Public',
      render: (row) => (
        <button
          type="button"
          onClick={() => onTogglePublic(row)}
          aria-label={row.is_public ? 'Set as private' : 'Set as public'}
          className={`rounded-full px-2 py-0.5 text-xs font-semibold transition-colors ${
            row.is_public
              ? 'bg-green-100 text-green-700 hover:bg-green-200'
              : 'bg-card border border-border text-foreground/50 hover:bg-primary/10'
          }`}
        >
          {row.is_public ? 'Public' : 'Private'}
        </button>
      ),
    },
    {
      key: 'actions',
      header: 'Actions',
      render: (row) => (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onEdit(row)}
            aria-label={`Edit profile ${row.name}`}
            className="flex items-center gap-1 rounded-lg border border-border bg-background px-2 py-1 text-xs font-semibold text-foreground hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <Pencil size={12} aria-hidden="true" />
            Edit
          </button>
          <button
            type="button"
            onClick={() => onDelete(row)}
            aria-label={`Delete profile ${row.name}`}
            className="flex items-center gap-1 rounded-lg border border-border bg-background px-2 py-1 text-xs font-semibold text-foreground hover:bg-red-50 hover:text-red-500 focus:outline-none focus:ring-2 focus:ring-ring"
          >
            Delete
          </button>
        </div>
      ),
    },
  ];
}

// ---------- Page --------------------------------------------------------------

export function ProfilesPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [confirmDelete, setConfirmDelete] = useState<ProfileResponse | null>(null);

  const { data, isLoading, isError, refetch } = useProfiles({
    entity: 'contact',
    page,
    page_size: pageSize,
  });

  const deleteMut = useDeleteProfile();

  const rows = data?.items ?? [];
  const total = data?.total ?? 0;
  const hasNoPublic = !isLoading && !isError && rows.length > 0 && !rows.some((r) => r.is_public);

  // Inline is_public toggle — requires useUpdateProfile per profile id.
  // We use a wrapper so we can call useMutation with the specific profile id.
  const handleTogglePublic = (profile: ProfileResponse) => {
    // Re-use profilesApi directly to avoid hook-at-render-time issues
    import('@/services/profilesApi')
      .then(({ profilesApi }) =>
        profilesApi.update(profile.id, { is_public: !profile.is_public }),
      )
      .then(() => {
        toast.success(
          profile.is_public
            ? `"${profile.name}" is now private`
            : `"${profile.name}" is now public`,
        );
        refetch();
      })
      .catch((err: AxiosError<ApiError>) => {
        toast.error(err.response?.data?.detail ?? 'Failed to update profile');
      });
  };

  const handleDelete = (profile: ProfileResponse) => {
    deleteMut.mutate(profile.id, {
      onSuccess: () => {
        toast.success(`Profile "${profile.name}" deleted`);
        setConfirmDelete(null);
      },
      onError: (err) => {
        const axiosErr = err as AxiosError<ApiError>;
        toast.error(axiosErr.response?.data?.detail ?? 'Failed to delete profile');
        setConfirmDelete(null);
      },
    });
  };

  const columns = ProfilesPageColumns({
    onEdit: (p) => navigate(`/profiles/${p.id}/edit`),
    onDelete: (p) => setConfirmDelete(p),
    onTogglePublic: handleTogglePublic,
  });

  return (
    <div className="flex h-screen flex-col">
      {/* Header */}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ClipboardList size={18} className="text-foreground/50" aria-hidden="true" />
            <h1 className="text-lg font-bold text-foreground">Profiles</h1>
          </div>
          <button
            type="button"
            onClick={() => navigate('/profiles/new')}
            aria-label="New Profile"
            className="flex min-h-[36px] items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98] focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <Plus size={15} aria-hidden="true" />
            New Profile
          </button>
        </div>
      </header>

      {/* Warning: no public profile */}
      {hasNoPublic && (
        <div className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800">
          <AlertTriangle size={15} aria-hidden="true" />
          <span>
            No profile is currently set as public. The newcomer form at{' '}
            <code className="font-mono text-xs">/welcome</code> will return 404.
          </span>
        </div>
      )}

      {/* Table */}
      <main className="flex-1 overflow-y-auto">
        {isError ? (
          <ErrorState message="Failed to load profiles" onRetry={() => refetch()} />
        ) : (
          <DataTable<ProfileResponse>
            columns={columns}
            rows={rows}
            getRowKey={(r) => r.id}
            isLoading={isLoading}
            emptyState={
              <EmptyState
                icon={ClipboardList}
                title="No profiles yet"
                description='Create your first profile with "New Profile"'
              />
            }
          />
        )}
      </main>

      {/* Pagination */}
      {!isError && (
        <div className="border-t border-border bg-card px-3 py-1 pb-safe">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onPageChange={setPage}
          />
        </div>
      )}

      {/* Confirm delete dialog */}
      {confirmDelete && (
        <ConfirmDialog
          message={`Delete profile "${confirmDelete.name}"? This action cannot be undone.`}
          confirmLabel="Delete"
          destructive
          onConfirm={() => handleDelete(confirmDelete)}
          onCancel={() => setConfirmDelete(null)}
        />
      )}
    </div>
  );
}
