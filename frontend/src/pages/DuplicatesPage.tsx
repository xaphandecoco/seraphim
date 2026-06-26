// S11 — DuplicatesPage: Find & Merge Duplicates.
// Route: /duplicates (VolunteerRoute — volunteer+; Rules tab admin-only)

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Copy, History, Settings2, Search, AlertTriangle } from 'lucide-react';
import { useAuthStore } from '@/store/authStore';
import { findCandidates, listRuleSets, getMergeHistory } from '@/services/dedupe';
import type { CandidatePair, CandidatePairList } from '@/types/dedupe';
import { DataTable } from '@/components/ui/DataTable';
import type { Column } from '@/components/ui/DataTable';
import { Pagination } from '@/components/ui/Pagination';
import { LoadingState, EmptyState, ErrorState } from '@/components/ui/StateViews';
import { MergeModal } from '@/components/dedupe/MergeModal';
import { DedupeRuleEditor } from '@/components/dedupe/DedupeRuleEditor';

// ─── Segment tabs ─────────────────────────────────────────────────────────────

type Tab = 'candidates' | 'rules' | 'history';

// ─── Candidates tab ───────────────────────────────────────────────────────────

const PAGE_SIZE = 25;

function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 100
      ? 'bg-green-500/15 text-green-700'
      : score >= 80
        ? 'bg-primary/15 text-primary'
        : 'bg-foreground/10 text-foreground/60';
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-bold ${color}`}>
      {score}
    </span>
  );
}

function SameNameChip() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-destructive/15 px-2.5 py-0.5 text-[10px] font-bold text-destructive">
      <AlertTriangle size={9} aria-hidden="true" />
      same name — verify
    </span>
  );
}

function ContactCell({ c }: { c: CandidatePair['contact_a'] }) {
  const name = `${c.first_name} ${c.last_name}`.trim() || '(unnamed)';
  return (
    <div>
      <p className="font-semibold text-foreground text-xs">{name}</p>
      {c.email && <p className="text-[11px] text-foreground/50 truncate max-w-[140px]">{c.email}</p>}
      {c.phone && <p className="text-[11px] text-foreground/50">{c.phone}</p>}
      <p className="text-[10px] text-foreground/40 mt-0.5">
        {c.participant_count} ev · {c.face_sample_count} faces
      </p>
    </div>
  );
}

function CandidatesTab() {
  const [page, setPage] = useState(1);
  const [ruleSetId, setRuleSetId] = useState<number | undefined>(undefined);
  const [contactType, setContactType] = useState<string>('');
  const [hasSearched, setHasSearched] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchResult, setSearchResult] = useState<CandidatePairList | null>(null);
  const [selectedPair, setSelectedPair] = useState<CandidatePair | null>(null);

  const { data: ruleSets = [] } = useQuery({
    queryKey: ['dedupe', 'rule-sets'],
    queryFn: listRuleSets,
  });

  async function handleFind(newPage = 1) {
    setHasSearched(true);
    setPage(newPage);
    setIsSearching(true);
    setSearchError(null);
    try {
      const data = await findCandidates({
        rule_set_id: ruleSetId,
        contact_type: contactType || undefined,
        page: newPage,
        page_size: PAGE_SIZE,
      });
      setSearchResult(data);
    } catch (err: unknown) {
      const ax = err as { response?: { data?: { detail?: string } } };
      const msg = ax.response?.data?.detail ?? 'Failed to find candidates';
      setSearchError(msg);
      toast.error(msg);
    } finally {
      setIsSearching(false);
    }
  }

  const pairs = searchResult?.items ?? [];
  const total = searchResult?.total ?? 0;
  const pagedLoading = isSearching;
  const pagedError = !!searchError;

  const columns: Column<CandidatePair>[] = [
    {
      key: 'contact_a',
      header: 'Contact A',
      render: (row) => <ContactCell c={row.contact_a} />,
    },
    {
      key: 'contact_b',
      header: 'Contact B',
      render: (row) => <ContactCell c={row.contact_b} />,
    },
    {
      key: 'score',
      header: 'Score',
      render: (row) => (
        <div className="space-y-1">
          <ScoreBadge score={row.score} />
          {row.same_name && <SameNameChip />}
        </div>
      ),
    },
    {
      key: 'matched_fields',
      header: 'Matched',
      render: (row) => (
        <div className="flex flex-wrap gap-1">
          {row.matched_fields.map((f) => (
            <span
              key={f}
              className="rounded-full border border-border px-2 py-0.5 text-[10px] text-foreground/60"
            >
              {f.replace(/_/g, ' ')}
            </span>
          ))}
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex flex-wrap gap-3 rounded-xl border border-border bg-card p-4">
        <div className="flex-1 min-w-[140px]">
          <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-foreground/50">
            Rule set
          </label>
          <select
            value={ruleSetId ?? ''}
            onChange={(e) => setRuleSetId(e.target.value ? Number(e.target.value) : undefined)}
            className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="">Default</option>
            {ruleSets.map((rs) => (
              <option key={rs.id} value={rs.id}>
                {rs.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex-1 min-w-[120px]">
          <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-foreground/50">
            Contact type
          </label>
          <select
            value={contactType}
            onChange={(e) => setContactType(e.target.value)}
            className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="">All types</option>
            <option value="Individual">Individual</option>
            <option value="Organization">Organization</option>
          </select>
        </div>
        <div className="flex items-end">
          <button
            type="button"
            onClick={() => handleFind(1)}
            disabled={pagedLoading}
            className="flex h-10 items-center gap-2 rounded-xl bg-primary px-5 text-sm font-bold text-primary-foreground shadow-sm hover:bg-primary/85 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <Search size={14} aria-hidden="true" />
            Find duplicates
          </button>
        </div>
      </div>

      {/* Results */}
      {!hasSearched && (
        <EmptyState
          icon={Copy}
          title="Run the finder to detect duplicates"
          description="Select a rule set and click Find duplicates."
        />
      )}

      {hasSearched && (pagedLoading ? (
        <LoadingState message="Scanning for duplicates…" />
      ) : pagedError ? (
        <ErrorState message={searchError ?? 'Failed to load candidates'} onRetry={() => handleFind(page)} />
      ) : (
        <>
          <DataTable<CandidatePair>
            columns={columns}
            rows={pairs}
            getRowKey={(row) => `${row.contact_a.id}-${row.contact_b.id}`}
            onRowClick={(row) => setSelectedPair(row)}
            isLoading={false}
            emptyState={
              <EmptyState
                icon={Copy}
                title="No likely duplicates found"
                description="Try a different rule set or lower the threshold."
              />
            }
          />
          {total > PAGE_SIZE && (
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={total}
              onPageChange={(p) => handleFind(p)}
            />
          )}
        </>
      ))}

      {/* Merge modal */}
      {selectedPair && (
        <MergeModal pair={selectedPair} onClose={() => setSelectedPair(null)} />
      )}
    </div>
  );
}

// ─── History tab ──────────────────────────────────────────────────────────────

function HistoryTab() {
  const [page, setPage] = useState(1);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['dedupe', 'history', page],
    queryFn: () => getMergeHistory(page, PAGE_SIZE),
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  if (isLoading) return <LoadingState message="Loading merge history…" />;
  if (isError) return <ErrorState message="Failed to load history" onRetry={() => refetch()} />;

  if (items.length === 0) {
    return (
      <EmptyState
        icon={History}
        title="No merges yet"
        description="Merge history will appear here after your first merge."
      />
    );
  }

  return (
    <div className="space-y-4">
      <ul className="space-y-2">
        {items.map((item) => {
          const at = new Date(item.created_at).toLocaleString();
          const actor =
            item.actor && typeof item.actor === 'object'
              ? ((item.actor as Record<string, unknown>).email as string | undefined) ??
                `user ${item.actor_id ?? ''}`
              : `user ${item.actor_id ?? ''}`;
          return (
            <li key={item.id} className="rounded-xl border border-border bg-card p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-foreground">
                    Survivor #{item.survivor_id}
                  </p>
                  <p className="text-xs text-foreground/50">
                    {at} · {actor}
                  </p>
                </div>
                <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-bold text-primary">
                  #{item.id}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
      {total > PAGE_SIZE && (
        <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
      )}
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function DuplicatesPage() {
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const [tab, setTab] = useState<Tab>('candidates');

  const tabs: Array<{ id: Tab; label: string; icon: typeof Copy; adminOnly?: boolean }> = [
    { id: 'candidates', label: 'Candidates', icon: Copy },
    { id: 'rules', label: 'Rules', icon: Settings2, adminOnly: true },
    { id: 'history', label: 'History', icon: History },
  ];

  const visibleTabs = tabs.filter((t) => !t.adminOnly || isAdmin);

  // If the active tab was hidden (e.g., role changed), fall back
  const activeTab = visibleTabs.find((t) => t.id === tab) ? tab : 'candidates';

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* Header */}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="mx-auto flex max-w-4xl items-center gap-3">
          <Copy size={20} className="shrink-0 text-primary" aria-hidden="true" />
          <h1 className="text-lg font-bold text-foreground">Find &amp; Merge Duplicates</h1>
        </div>
      </header>

      {/* Segment tabs */}
      <div className="sticky top-0 z-10 border-b border-border bg-card/95 backdrop-blur-sm">
        <div className="mx-auto flex max-w-4xl gap-1 px-4">
          {visibleTabs.map((t) => {
            const Icon = t.icon;
            const isActive = activeTab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                aria-current={isActive ? 'page' : undefined}
                className={`flex items-center gap-1.5 border-b-2 px-3 py-3 text-xs font-semibold transition-colors focus:outline-none ${
                  isActive
                    ? 'border-primary text-primary'
                    : 'border-transparent text-foreground/50 hover:text-foreground'
                }`}
              >
                <Icon size={13} aria-hidden="true" />
                {t.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Content */}
      <main className="mx-auto w-full max-w-4xl flex-1 p-4 pb-24">
        {activeTab === 'candidates' && <CandidatesTab />}
        {activeTab === 'rules' && isAdmin && <DedupeRuleEditor />}
        {activeTab === 'history' && <HistoryTab />}
      </main>
    </div>
  );
}
