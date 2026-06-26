import { useState, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Search, BookmarkPlus } from 'lucide-react';

import { useAuthStore } from '@/store/authStore';
import { searchApi } from '@/services/search';
import { FilterBuilder } from '@/components/search/FilterBuilder';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { Pagination } from '@/components/ui/Pagination';
import { StatusBadge, getTierTone } from '@/components/ui/StatusBadge';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/StateViews';
import type { CriteriaGroup, SavedSearch } from '@/types/search';
import type { ContactListItem } from '@/types';

const DEFAULT_CRITERIA: CriteriaGroup = { logic: 'and', conditions: [] };

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
    key: 'contact_type',
    header: 'Type',
    render: (row) => (
      <div className="flex flex-wrap gap-1">
        {row.contact_type && <StatusBadge label={row.contact_type} tone="muted" />}
        {row.contact_subtype && (
          <StatusBadge label={row.contact_subtype} tone="muted" />
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

export function AdvancedSearchPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const user = useAuthStore((s) => s.user);
  const canSave = isAdmin || user?.role === 'volunteer';

  // Pre-populate from saved search passed via router state
  const locationState = location.state as { savedSearch?: SavedSearch } | null;
  const initialCriteria =
    locationState?.savedSearch?.criteria &&
    'logic' in locationState.savedSearch.criteria
      ? (locationState.savedSearch.criteria as CriteriaGroup)
      : DEFAULT_CRITERIA;

  const [criteria, setCriteria] = useState<CriteriaGroup>(initialCriteria);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [hasSearched, setHasSearched] = useState(false);
  const [searchTrigger, setSearchTrigger] = useState(0);

  // Save dialog
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [saveName, setSaveName] = useState(
    locationState?.savedSearch?.name ?? '',
  );

  // Field registry
  const {
    data: fieldData,
    isLoading: fieldsLoading,
    isError: fieldsError,
  } = useQuery({
    queryKey: ['search-fields'],
    queryFn: searchApi.getFields,
    staleTime: 5 * 60 * 1000,
  });
  const fields = fieldData?.fields ?? [];

  // Search results — `searchTrigger` in key forces refetch on every click
  const {
    data: results,
    isLoading: searching,
    isError: searchError,
    refetch,
  } = useQuery({
    queryKey: ['search-results', criteria, page, pageSize, searchTrigger],
    queryFn: () => searchApi.search({ criteria, page, page_size: pageSize }),
    enabled: hasSearched && searchTrigger > 0,
    staleTime: 0,
  });

  const handleSearch = useCallback(() => {
    setPage(1);
    setHasSearched(true);
    setSearchTrigger((t) => t + 1);
  }, []);

  // Save search mutation
  const saveMutation = useMutation({
    mutationFn: (name: string) => searchApi.createSavedSearch({ name, criteria }),
    onSuccess: (saved) => {
      toast.success(`Saved search "${saved.name}"`);
      queryClient.invalidateQueries({ queryKey: ['saved-searches'] });
      setShowSaveDialog(false);
      setSaveName('');
    },
    onError: (err: unknown) => {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Failed to save search');
    },
  });

  if (fieldsLoading) {
    return <LoadingState message="Loading search fields…" />;
  }

  if (fieldsError) {
    return (
      <ErrorState
        message="Failed to load search fields"
        onRetry={() => queryClient.invalidateQueries({ queryKey: ['search-fields'] })}
      />
    );
  }

  return (
    <div className="flex h-screen flex-col">
      {/* Header */}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Search size={18} className="text-foreground/50" aria-hidden="true" />
            <h1 className="text-lg font-bold text-foreground">Advanced Search</h1>
          </div>
          <div className="flex items-center gap-2">
            {canSave && (
              <button
                type="button"
                onClick={() => setShowSaveDialog(true)}
                className="flex min-h-[36px] items-center gap-1.5 rounded-xl border border-border bg-card px-3 py-1.5 text-sm font-semibold text-foreground/70 hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring"
                aria-label="Save search"
              >
                <BookmarkPlus size={15} aria-hidden="true" />
                Save
              </button>
            )}
            <button
              type="button"
              onClick={handleSearch}
              className="flex min-h-[36px] items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98] focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <Search size={15} aria-hidden="true" />
              Search
            </button>
          </div>
        </div>
      </header>

      {/* Filter builder */}
      <div className="max-h-[45vh] overflow-y-auto border-b border-border bg-background p-3">
        <FilterBuilder value={criteria} fields={fields} onChange={setCriteria} />
      </div>

      {/* Results */}
      <main className="flex-1 overflow-y-auto">
        {!hasSearched ? (
          <EmptyState
            icon={Search}
            title="Build your query above"
            description="Add conditions and click Search to find contacts"
          />
        ) : searchError ? (
          <ErrorState message="Search failed" onRetry={() => refetch()} />
        ) : (
          <DataTable<ContactListItem>
            columns={columns}
            rows={results?.items ?? []}
            getRowKey={(row) => row.id}
            onRowClick={(row) => navigate(`/contacts/${row.id}`)}
            isLoading={searching}
            emptyState={
              <EmptyState
                icon={Search}
                title="No results"
                description="Try adjusting your search criteria"
              />
            }
          />
        )}
      </main>

      {/* Pagination */}
      {hasSearched && !searchError && (
        <div className="border-t border-border bg-card px-3 py-1">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={results?.total ?? 0}
            onPageChange={(p) => {
              setPage(p);
              setSearchTrigger((t) => t + 1);
            }}
            onPageSizeChange={(s) => {
              setPageSize(s);
              setPage(1);
              setSearchTrigger((t) => t + 1);
            }}
          />
        </div>
      )}

      {/* Save search dialog */}
      {showSaveDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40"
          onClick={() => setShowSaveDialog(false)}
        >
          <div
            className="w-full max-w-sm rounded-2xl border border-border bg-card p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="mb-4 text-base font-bold text-foreground">Save Search</h2>
            <label className="sr-only" htmlFor="save-search-name">
              Search name
            </label>
            <input
              id="save-search-name"
              type="text"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              placeholder="Search name…"
              autoFocus
              className="mb-4 h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowSaveDialog(false)}
                className="rounded-xl border border-border px-4 py-2 text-sm font-semibold text-foreground/70 hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  if (saveName.trim()) saveMutation.mutate(saveName.trim());
                }}
                disabled={!saveName.trim() || saveMutation.isPending}
                className="rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/85 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
              >
                {saveMutation.isPending ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
