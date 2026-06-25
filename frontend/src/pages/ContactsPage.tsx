// S15: gate contacts from viewer

import { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Users, Plus, Search } from 'lucide-react';

import { useAuthStore } from '@/store/authStore';
import { useContacts } from '@/hooks/useContacts';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { Pagination } from '@/components/ui/Pagination';
import { StatusBadge, getTierTone } from '@/components/ui/StatusBadge';
import { EmptyState, ErrorState } from '@/components/ui/StateViews';

import type { ContactListItem } from '@/types';

// ---------- Helpers -----------------------------------------------------------

const CONTACT_TYPE_OPTIONS = ['individual', 'organization', 'household'] as const;
const TIER_OPTIONS = ['tier0', 'tier1', 'tier2', 'tier3'] as const;

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timerRef.current);
  }, [value, delay]);

  return debounced;
}

// ---------- Columns ----------------------------------------------------------

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
        {row.contact_type && (
          <StatusBadge label={row.contact_type} tone="muted" />
        )}
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
  {
    key: 'email',
    header: 'Contact',
    render: (row) => (
      <div className="space-y-0.5">
        {row.email && <p className="text-xs text-foreground/70">{row.email}</p>}
        {row.phone && <p className="text-xs text-foreground/70">{row.phone}</p>}
      </div>
    ),
  },
];

// ---------- Main page --------------------------------------------------------

export function ContactsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isAdmin = useAuthStore((s) => s.isAdmin);

  const [searchInput, setSearchInput] = useState('');
  const search = useDebounce(searchInput, 300);

  const [contactTypeFilter, setContactTypeFilter] = useState<string | undefined>();
  const [tierFilter, setTierFilter] = useState<string | undefined>();
  const [isRegularFilter, setIsRegularFilter] = useState<boolean | undefined>();
  const [includeDeleted, setIncludeDeleted] = useState(false);

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [search, contactTypeFilter, tierFilter, isRegularFilter, includeDeleted]);

  const filters = useMemo(
    () => ({
      search: search || undefined,
      contact_type: contactTypeFilter,
      tier: tierFilter,
      is_regular: isRegularFilter,
      include_deleted: includeDeleted || undefined,
      page,
      page_size: pageSize,
    }),
    [search, contactTypeFilter, tierFilter, isRegularFilter, includeDeleted, page, pageSize],
  );

  const { data, isLoading, isError, refetch } = useContacts(filters);

  const rows = data?.items ?? [];
  const total = data?.total ?? 0;

  // Prefetch next page
  useEffect(() => {
    const totalPages = Math.ceil(total / pageSize);
    if (page < totalPages) {
      queryClient.prefetchQuery({
        queryKey: ['contacts', { ...filters, page: page + 1 }],
        queryFn: () => import('@/services/contacts').then(({ contactsApi }) =>
          contactsApi.list({ ...filters, page: page + 1 }),
        ),
      });
    }
  }, [page, total, pageSize, queryClient, filters]);

  const toggleChip = <T,>(
    current: T | undefined,
    value: T,
    setter: (v: T | undefined) => void,
  ) => {
    setter(current === value ? undefined : value);
  };

  return (
    <div className="flex h-screen flex-col">
      {/* Header */}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Users size={18} className="text-foreground/50" aria-hidden="true" />
            <h1 className="text-lg font-bold text-foreground">Contacts</h1>
          </div>
          <button
            type="button"
            onClick={() => navigate('/contacts/new')}
            className="flex min-h-[36px] items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98] focus:outline-none focus:ring-2 focus:ring-ring"
            aria-label="New Contact"
          >
            <Plus size={15} aria-hidden="true" />
            New Contact
          </button>
        </div>
      </header>

      {/* Search + filters */}
      <div className="border-b border-border bg-background px-3 py-3 space-y-2">
        {/* Search input */}
        <div className="relative">
          <Search
            size={16}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-foreground/40"
            aria-hidden="true"
          />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search contacts…"
            aria-label="Search contacts"
            className="h-10 w-full rounded-xl border border-border bg-card pl-9 pr-3 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
          />
        </div>

        {/* Filter chips */}
        <div className="flex flex-wrap gap-2" aria-label="Filters">
          {/* contact_type chips */}
          {CONTACT_TYPE_OPTIONS.map((ct) => (
            <button
              key={ct}
              type="button"
              aria-pressed={contactTypeFilter === ct}
              onClick={() => toggleChip(contactTypeFilter, ct, setContactTypeFilter)}
              className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
                contactTypeFilter === ct
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'border-border bg-card text-foreground/60 hover:bg-primary/10 hover:text-primary'
              }`}
            >
              {ct.charAt(0).toUpperCase() + ct.slice(1)}
            </button>
          ))}

          {/* tier chips */}
          {TIER_OPTIONS.map((t) => (
            <button
              key={t}
              type="button"
              aria-pressed={tierFilter === t}
              onClick={() => toggleChip(tierFilter, t, setTierFilter)}
              className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
                tierFilter === t
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'border-border bg-card text-foreground/60 hover:bg-primary/10 hover:text-primary'
              }`}
            >
              {t.toUpperCase()}
            </button>
          ))}

          {/* is_regular chip */}
          <button
            type="button"
            aria-pressed={isRegularFilter === true}
            onClick={() => toggleChip(isRegularFilter, true, setIsRegularFilter)}
            className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
              isRegularFilter === true
                ? 'border-primary bg-primary text-primary-foreground'
                : 'border-border bg-card text-foreground/60 hover:bg-primary/10 hover:text-primary'
            }`}
          >
            Regular
          </button>

          {/* Admin-only: include deleted toggle */}
          {isAdmin && (
            <button
              type="button"
              aria-pressed={includeDeleted}
              onClick={() => setIncludeDeleted((v) => !v)}
              className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
                includeDeleted
                  ? 'border-red-500 bg-red-500 text-white'
                  : 'border-border bg-card text-foreground/60 hover:bg-red-50 hover:text-red-600'
              }`}
            >
              Include Deleted
            </button>
          )}
        </div>
      </div>

      {/* Table */}
      <main className="flex-1 overflow-y-auto">
        {isError ? (
          <ErrorState message="Failed to load contacts" onRetry={() => refetch()} />
        ) : (
          <DataTable<ContactListItem>
            columns={columns}
            rows={rows}
            getRowKey={(row) => row.id}
            onRowClick={(row) => navigate(`/contacts/${row.id}`)}
            isLoading={isLoading}
            emptyState={
              <EmptyState
                icon={Users}
                title="No contacts found"
                description="Try adjusting your search or filters"
              />
            }
          />
        )}
      </main>

      {/* Pagination footer */}
      {!isError && (
        <div className="border-t border-border bg-card px-3 py-1 pb-safe">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onPageChange={setPage}
            onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
          />
        </div>
      )}
    </div>
  );
}
