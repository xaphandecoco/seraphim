import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft, Zap, Database, Edit2, Check, X } from 'lucide-react';

import { useAuthStore } from '@/store/authStore';
import { searchApi } from '@/services/search';
import { FilterBuilder } from '@/components/search/FilterBuilder';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { Pagination } from '@/components/ui/Pagination';
import { StatusBadge, getTierTone } from '@/components/ui/StatusBadge';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/StateViews';
import type { CriteriaGroup } from '@/types/search';
import type { ContactListItem } from '@/types';

const columns: Column<ContactListItem>[] = [
  {
    key: 'display_name',
    header: 'Name',
    render: (row) => (
      <div>
        <span className="font-medium text-foreground">{row.display_name}</span>
        {row.nickname && (
          <span className="ml-1 text-xs text-foreground/50">"{row.nickname}"</span>
        )}
      </div>
    ),
  },
  {
    key: 'tier',
    header: 'Tier',
    render: (row) =>
      row.tier ? (
        <StatusBadge label={row.tier} tone={getTierTone(row.tier)} />
      ) : (
        <StatusBadge label="Unrated" tone="muted" />
      ),
  },
  {
    key: 'status',
    header: 'Status',
    render: (row) => (
      <div className="flex flex-wrap gap-1">
        {row.is_regular && <StatusBadge label="Regular" tone="active" />}
        {row.is_connected && <StatusBadge label="Connected" tone="warning" />}
      </div>
    ),
  },
];

export function GroupDetailPage() {
  const { id } = useParams<{ id: string }>();
  const groupId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isAdmin = useAuthStore((s) => s.isAdmin);

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [editingCriteria, setEditingCriteria] = useState(false);
  const [draftCriteria, setDraftCriteria] = useState<CriteriaGroup>({
    logic: 'and',
    conditions: [],
  });

  // Group detail
  const {
    data: group,
    isLoading: groupLoading,
    isError: groupError,
    refetch: refetchGroup,
  } = useQuery({
    queryKey: ['group', groupId],
    queryFn: () => searchApi.getGroup(groupId),
    enabled: !isNaN(groupId),
  });

  // Group members
  const {
    data: membersData,
    isLoading: membersLoading,
    isError: membersError,
    refetch: refetchMembers,
  } = useQuery({
    queryKey: ['group-members', groupId, page, pageSize],
    queryFn: () => searchApi.getGroupMembers(groupId, page, pageSize),
    enabled: !isNaN(groupId) && !!group,
  });

  // Field registry — only needed when admin is editing a smart group's criteria
  const { data: fieldData } = useQuery({
    queryKey: ['search-fields'],
    queryFn: searchApi.getFields,
    enabled: group?.group_type === 'smart' && isAdmin,
    staleTime: 5 * 60 * 1000,
  });
  const fields = fieldData?.fields ?? [];

  const updateCriteriaMutation = useMutation({
    mutationFn: (criteria: CriteriaGroup) =>
      searchApi.updateGroup(groupId, { criteria }),
    onSuccess: () => {
      toast.success('Criteria updated');
      queryClient.invalidateQueries({ queryKey: ['group', groupId] });
      queryClient.invalidateQueries({ queryKey: ['group-members', groupId] });
      setEditingCriteria(false);
    },
    onError: (err: unknown) => {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Failed to update criteria');
    },
  });

  if (groupLoading) return <LoadingState message="Loading group…" />;
  if (groupError || !group) {
    return (
      <ErrorState
        message="Failed to load group"
        onRetry={() => refetchGroup()}
      />
    );
  }

  const startEdit = () => {
    const existing = group.criteria;
    setDraftCriteria(
      existing && 'logic' in existing
        ? (existing as CriteriaGroup)
        : { logic: 'and', conditions: [] },
    );
    setEditingCriteria(true);
  };

  return (
    <div className="flex h-screen flex-col">
      {/* Header */}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => navigate('/groups')}
            aria-label="Back to groups"
            className="flex h-8 w-8 items-center justify-center rounded-full text-foreground/50 hover:bg-background hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <ArrowLeft size={18} aria-hidden="true" />
          </button>
          <div className="flex min-w-0 items-center gap-2">
            {group.group_type === 'smart' ? (
              <Zap size={16} className="shrink-0 text-primary" aria-hidden="true" />
            ) : (
              <Database
                size={16}
                className="shrink-0 text-foreground/50"
                aria-hidden="true"
              />
            )}
            <h1 className="truncate text-lg font-bold text-foreground">
              {group.name}
            </h1>
          </div>
          <StatusBadge
            label={group.group_type === 'smart' ? 'Smart' : 'Static'}
            tone={group.group_type === 'smart' ? 'active' : 'muted'}
          />
        </div>
      </header>

      {/* Smart group criteria panel — admin can edit */}
      {group.group_type === 'smart' && (
        <div className="border-b border-border bg-background p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-wide text-foreground/50">
              Criteria
            </p>
            {isAdmin && !editingCriteria && (
              <button
                type="button"
                onClick={startEdit}
                aria-label="Edit criteria"
                className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs font-semibold text-foreground/70 hover:bg-card focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <Edit2 size={12} aria-hidden="true" /> Edit
              </button>
            )}
            {isAdmin && editingCriteria && (
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setEditingCriteria(false)}
                  aria-label="Cancel edit"
                  className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs font-semibold text-foreground/70 hover:bg-card focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <X size={12} aria-hidden="true" /> Cancel
                </button>
                <button
                  type="button"
                  onClick={() => updateCriteriaMutation.mutate(draftCriteria)}
                  disabled={updateCriteriaMutation.isPending}
                  aria-label="Save criteria"
                  className="flex items-center gap-1 rounded-lg bg-primary px-2 py-1 text-xs font-semibold text-primary-foreground hover:bg-primary/85 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <Check size={12} aria-hidden="true" />{' '}
                  {updateCriteriaMutation.isPending ? 'Saving…' : 'Save'}
                </button>
              </div>
            )}
          </div>

          {editingCriteria ? (
            <FilterBuilder
              value={draftCriteria}
              fields={fields}
              onChange={setDraftCriteria}
            />
          ) : group.criteria ? (
            <pre className="overflow-x-auto rounded-lg bg-card p-2 text-xs text-foreground/70">
              {JSON.stringify(group.criteria, null, 2)}
            </pre>
          ) : (
            <p className="text-xs text-foreground/40">No criteria defined</p>
          )}
        </div>
      )}

      {/* Members table */}
      <main className="flex-1 overflow-y-auto">
        {membersError ? (
          <ErrorState
            message="Failed to load members"
            onRetry={() => refetchMembers()}
          />
        ) : (
          <DataTable<ContactListItem>
            columns={columns}
            rows={membersData?.items ?? []}
            getRowKey={(row) => row.id}
            onRowClick={(row) => navigate(`/contacts/${row.id}`)}
            isLoading={membersLoading}
            emptyState={
              <EmptyState
                title="No members"
                description={
                  group.group_type === 'smart'
                    ? 'No contacts match the current criteria'
                    : 'This group has no members yet'
                }
              />
            }
          />
        )}
      </main>

      {/* Pagination */}
      {!membersError && (
        <div className="border-t border-border bg-card px-3 py-1">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={membersData?.total ?? 0}
            onPageChange={setPage}
            onPageSizeChange={(s) => {
              setPageSize(s);
              setPage(1);
            }}
          />
        </div>
      )}
    </div>
  );
}
