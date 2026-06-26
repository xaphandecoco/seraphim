import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Users2, Plus, Trash2, Zap, Database } from 'lucide-react';

import { useAuthStore } from '@/store/authStore';
import { searchApi } from '@/services/search';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/StateViews';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { StatusBadge } from '@/components/ui/StatusBadge';
import type { GroupResponse, GroupType } from '@/types/search';

export function GroupsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const user = useAuthStore((s) => s.user);
  const canCreate = isAdmin || user?.role === 'volunteer';

  const [deleteTarget, setDeleteTarget] = useState<GroupResponse | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newType, setNewType] = useState<GroupType>('static');

  const {
    data: groups = [],
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['groups'],
    queryFn: () => searchApi.listGroups(),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => searchApi.deleteGroup(id),
    onSuccess: () => {
      toast.success('Group deleted');
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      setDeleteTarget(null);
    },
    onError: (err: unknown) => {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Failed to delete group');
      setDeleteTarget(null);
    },
  });

  const createMutation = useMutation({
    mutationFn: () =>
      searchApi.createGroup({ name: newName.trim(), group_type: newType }),
    onSuccess: (group) => {
      toast.success(`Group "${group.name}" created`);
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      setShowCreate(false);
      setNewName('');
      navigate(`/groups/${group.id}`);
    },
    onError: (err: unknown) => {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Failed to create group');
    },
  });

  if (isLoading) return <LoadingState message="Loading groups…" />;
  if (isError)
    return <ErrorState message="Failed to load groups" onRetry={() => refetch()} />;

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Users2 size={18} className="text-foreground/50" aria-hidden="true" />
            <h1 className="text-lg font-bold text-foreground">Groups</h1>
          </div>
          {canCreate && (
            <button
              type="button"
              onClick={() => setShowCreate(true)}
              className="flex min-h-[36px] items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98] focus:outline-none focus:ring-2 focus:ring-ring"
              aria-label="New Group"
            >
              <Plus size={15} aria-hidden="true" />
              New Group
            </button>
          )}
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-4">
        {groups.length === 0 ? (
          <EmptyState
            icon={Users2}
            title="No groups yet"
            description="Create a static or smart group to segment your contacts"
          />
        ) : (
          <ul className="space-y-2" aria-label="Groups">
            {groups.map((g) => (
              <li key={g.id}>
                <button
                  type="button"
                  onClick={() => navigate(`/groups/${g.id}`)}
                  className="w-full rounded-xl border border-border bg-card p-4 text-left shadow-sm transition-colors hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      {g.group_type === 'smart' ? (
                        <Zap
                          size={16}
                          className="shrink-0 text-primary"
                          aria-hidden="true"
                        />
                      ) : (
                        <Database
                          size={16}
                          className="shrink-0 text-foreground/50"
                          aria-hidden="true"
                        />
                      )}
                      <div className="min-w-0">
                        <p className="truncate font-semibold text-foreground">
                          {g.name}
                        </p>
                        <div className="mt-1 flex items-center gap-2">
                          <StatusBadge
                            label={g.group_type === 'smart' ? 'Smart' : 'Static'}
                            tone={g.group_type === 'smart' ? 'active' : 'muted'}
                          />
                          {g.member_count !== null &&
                            g.member_count !== undefined && (
                              <span className="text-xs text-foreground/50">
                                {g.member_count} members
                              </span>
                            )}
                        </div>
                      </div>
                    </div>
                    {(isAdmin || g.owner_id === user?.id) && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setDeleteTarget(g);
                        }}
                        aria-label={`Delete group "${g.name}"`}
                        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-foreground/40 hover:bg-background hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                      >
                        <Trash2 size={15} aria-hidden="true" />
                      </button>
                    )}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </main>

      {/* Create group dialog */}
      {showCreate && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40"
          onClick={() => setShowCreate(false)}
        >
          <div
            className="w-full max-w-sm rounded-2xl border border-border bg-card p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="mb-4 text-base font-bold text-foreground">New Group</h2>
            <label className="sr-only" htmlFor="new-group-name">
              Group name
            </label>
            <input
              id="new-group-name"
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Group name…"
              autoFocus
              className="mb-3 h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
            <div className="mb-4 flex gap-2">
              {(['static', 'smart'] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  aria-pressed={newType === t}
                  onClick={() => setNewType(t)}
                  className={`flex-1 rounded-lg border px-3 py-2 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
                    newType === t
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'border-border bg-card text-foreground/60 hover:bg-background'
                  }`}
                >
                  {t === 'smart' ? 'Smart (live)' : 'Static (frozen)'}
                </button>
              ))}
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowCreate(false)}
                className="rounded-xl border border-border px-4 py-2 text-sm font-semibold text-foreground/70 hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  if (newName.trim()) createMutation.mutate();
                }}
                disabled={!newName.trim() || createMutation.isPending}
                className="rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/85 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
              >
                {createMutation.isPending ? 'Creating…' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteTarget && (
        <ConfirmDialog
          message={`Delete group "${deleteTarget.name}"? This cannot be undone.`}
          confirmLabel="Delete"
          destructive
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
