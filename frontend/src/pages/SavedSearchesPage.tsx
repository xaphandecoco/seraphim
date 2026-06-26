import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Bookmark, Trash2, Play } from 'lucide-react';

import { useAuthStore } from '@/store/authStore';
import { searchApi } from '@/services/search';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/StateViews';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { SavedSearch } from '@/types/search';

export function SavedSearchesPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const canManage = isAdmin || user?.role === 'volunteer';

  const [deleteTarget, setDeleteTarget] = useState<SavedSearch | null>(null);

  const {
    data: searches = [],
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['saved-searches'],
    queryFn: searchApi.listSavedSearches,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => searchApi.deleteSavedSearch(id),
    onSuccess: () => {
      toast.success('Saved search deleted');
      queryClient.invalidateQueries({ queryKey: ['saved-searches'] });
      setDeleteTarget(null);
    },
    onError: (err: unknown) => {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Failed to delete saved search');
      setDeleteTarget(null);
    },
  });

  if (isLoading) return <LoadingState message="Loading saved searches…" />;
  if (isError)
    return (
      <ErrorState
        message="Failed to load saved searches"
        onRetry={() => refetch()}
      />
    );

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-2">
          <Bookmark size={18} className="text-foreground/50" aria-hidden="true" />
          <h1 className="text-lg font-bold text-foreground">Saved Searches</h1>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-4">
        {searches.length === 0 ? (
          <EmptyState
            icon={Bookmark}
            title="No saved searches"
            description="Run an advanced search and save it to see it here"
          />
        ) : (
          <ul className="space-y-2" aria-label="Saved searches">
            {searches.map((s) => (
              <li
                key={s.id}
                className="rounded-xl border border-border bg-card p-4 shadow-sm"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-foreground">{s.name}</p>
                    <p className="text-xs text-foreground/50">
                      Saved {new Date(s.created_at).toLocaleDateString()}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      onClick={() =>
                        navigate('/contacts/search', {
                          state: { savedSearch: s },
                        })
                      }
                      aria-label={`Run search "${s.name}"`}
                      className="flex h-8 w-8 items-center justify-center rounded-full text-primary hover:bg-primary/10 focus:outline-none focus:ring-2 focus:ring-ring"
                    >
                      <Play size={15} aria-hidden="true" />
                    </button>
                    {(canManage || s.owner_id === user?.id) && (
                      <button
                        type="button"
                        onClick={() => setDeleteTarget(s)}
                        aria-label={`Delete search "${s.name}"`}
                        className="flex h-8 w-8 items-center justify-center rounded-full text-foreground/40 hover:bg-background hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                      >
                        <Trash2 size={15} aria-hidden="true" />
                      </button>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </main>

      {deleteTarget && (
        <ConfirmDialog
          message={`Delete saved search "${deleteTarget.name}"? This cannot be undone.`}
          confirmLabel="Delete"
          destructive
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
