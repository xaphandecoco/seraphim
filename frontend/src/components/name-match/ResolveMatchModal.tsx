import { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { X, Check, Loader2 } from 'lucide-react';
import { contactsApi } from '@/services/contacts';
import { resolveQueueItem, unmatchQueueItem } from '@/services/nameMatch';
import type { ReviewQueueItem, MatchCandidate } from '@/types/nameMatch';

interface ContactSearchResult {
  id: number;
  display_name: string;
  email?: string | null;
  phone?: string | null;
}

interface Props {
  item: ReviewQueueItem;
  open: boolean;
  onClose: () => void;
  onResolved: () => void;
}

function MethodChip({ method }: { method: string }) {
  const labels: Record<string, string> = {
    alias: 'Alias',
    fuzzy_forward: 'Fuzzy',
    fuzzy_reversed: 'Reversed',
    fuzzy_nickname: 'Nickname',
    claude: 'AI',
  };
  return (
    <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold bg-muted text-muted-foreground uppercase tracking-wide">
      {labels[method] ?? method}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: 'bg-amber-50 text-amber-700',
    matched: 'bg-green-50 text-green-700',
    unmatched: 'bg-red-50 text-red-600',
    skipped: 'bg-gray-100 text-gray-500',
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${styles[status] ?? 'bg-muted text-muted-foreground'}`}>
      {status}
    </span>
  );
}

export function ResolveMatchModal({ item, open, onClose, onResolved }: Props) {
  const [selectedContactId, setSelectedContactId] = useState<number | null>(null);
  const [selectedContactName, setSelectedContactName] = useState<string>('');
  const [teachAlias, setTeachAlias] = useState(false);
  const [aliasText, setAliasText] = useState(item.normalized_name);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<ContactSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Pre-check teach alias when no candidate or method includes claude
  useEffect(() => {
    if (!open) return;
    setSelectedContactId(null);
    setSelectedContactName('');
    setAliasText(item.normalized_name);
    setSearchQuery('');
    setSearchResults([]);
    const hasClaudeCandidate = item.candidates.some((c) => c.method === 'claude');
    setTeachAlias(item.candidates.length === 0 || hasClaudeCandidate);
  }, [open, item]);

  useEffect(() => {
    if (!searchQuery.trim() || searchQuery.length < 2) {
      setSearchResults([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const resp = await contactsApi.list({ search: searchQuery, page_size: 5 });
        if (cancelled) return;
        setSearchResults(
          resp.items.map((c) => ({
            id: c.id,
            display_name: c.display_name,
            email: c.email,
            phone: c.phone,
          })),
        );
      } catch (err) {
        if (cancelled) return;
        setSearchResults([]);
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        toast.error(detail || 'Contact search failed');
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 300);
    // Cancel a stale in-flight search when the query changes so a slow earlier
    // response can't overwrite newer results.
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [searchQuery]);

  const handleSelectCandidate = (candidate: MatchCandidate) => {
    setSelectedContactId(candidate.contact_id);
    setSelectedContactName(candidate.display_name);
    setSearchQuery('');
    setSearchResults([]);
  };

  const handleSelectSearchResult = (contact: ContactSearchResult) => {
    setSelectedContactId(contact.id);
    setSelectedContactName(contact.display_name);
    setSearchQuery('');
    setSearchResults([]);
  };

  const handleMatch = async () => {
    if (!selectedContactId) return;
    setSubmitting(true);
    try {
      await resolveQueueItem(item.id, {
        contact_id: selectedContactId,
        teach_alias: teachAlias,
        alias_text: teachAlias ? aliasText : undefined,
      });
      toast.success('Match resolved');
      onResolved();
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Resolution failed');
    } finally {
      setSubmitting(false);
    }
  };

  const handleNoMatch = async () => {
    setSubmitting(true);
    try {
      await unmatchQueueItem(item.id);
      toast.success('Marked as no match');
      onResolved();
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Resolution failed');
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/40"
        aria-hidden="true"
        onClick={onClose}
      />

      {/* Modal: bottom sheet on mobile, centered dialog on md+ */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Resolve name match"
        className="fixed inset-x-0 bottom-0 z-50 rounded-t-2xl border border-border bg-card shadow-2xl md:inset-0 md:m-auto md:h-fit md:max-h-[90vh] md:max-w-lg md:rounded-2xl"
      >
        <div className="flex max-h-[85vh] flex-col overflow-hidden md:max-h-[85vh]">
          {/* Header */}
          <div className="flex items-start justify-between border-b border-border px-5 py-4">
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Resolve match</p>
              <h2 className="mt-0.5 truncate text-xl font-bold text-foreground">{item.raw_name}</h2>
              <p className="mt-0.5 text-sm text-muted-foreground">{item.normalized_name}</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
                  {item.source}
                </span>
                {item.event_title && (
                  <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                    {item.event_title}
                  </span>
                )}
                <StatusBadge status={item.status} />
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close modal"
              className="ml-3 shrink-0 rounded-lg p-1 text-muted-foreground hover:bg-muted hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <X size={18} aria-hidden="true" />
            </button>
          </div>

          {/* Scrollable body */}
          <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
            {/* Candidates */}
            {item.candidates.length > 0 && (
              <section aria-label="Suggested candidates">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Suggested matches
                </p>
                <ul className="space-y-2" role="listbox" aria-label="Candidates">
                  {item.candidates.slice(0, 5).map((candidate) => {
                    const isSelected = selectedContactId === candidate.contact_id;
                    return (
                      <li key={candidate.contact_id}>
                        <button
                          type="button"
                          role="option"
                          aria-selected={isSelected}
                          onClick={() => handleSelectCandidate(candidate)}
                          className={`w-full rounded-xl border px-4 py-3 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
                            isSelected
                              ? 'border-primary bg-primary/10'
                              : 'border-border bg-background hover:border-primary/50 hover:bg-muted'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-semibold text-foreground">{candidate.display_name}</span>
                            <div className="flex shrink-0 items-center gap-1.5">
                              <span className="text-sm font-medium text-muted-foreground">
                                {Math.round(candidate.score * 100)}%
                              </span>
                              <MethodChip method={candidate.method} />
                              {isSelected && <Check size={14} className="text-primary" aria-hidden="true" />}
                            </div>
                          </div>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}

            {/* Contact search */}
            <section aria-label="Search contacts">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Select a different contact
              </p>
              <div className="relative">
                <input
                  type="search"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search by name…"
                  aria-label="Search contacts"
                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
                {searching && (
                  <Loader2
                    size={14}
                    className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-muted-foreground"
                    aria-hidden="true"
                  />
                )}
              </div>
              {searchResults.length > 0 && (
                <ul className="mt-1.5 space-y-1" role="listbox" aria-label="Contact search results">
                  {searchResults.map((contact) => {
                    const isSelected = selectedContactId === contact.id;
                    return (
                      <li key={contact.id}>
                        <button
                          type="button"
                          role="option"
                          aria-selected={isSelected}
                          onClick={() => handleSelectSearchResult(contact)}
                          className={`w-full rounded-xl border px-4 py-2.5 text-left text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
                            isSelected
                              ? 'border-primary bg-primary/10'
                              : 'border-border bg-background hover:border-primary/50 hover:bg-muted'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-medium text-foreground">{contact.display_name}</span>
                            {isSelected && <Check size={14} className="text-primary" aria-hidden="true" />}
                          </div>
                          {contact.email && (
                            <span className="text-xs text-muted-foreground">{contact.email}</span>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
              {selectedContactId && (
                <p className="mt-2 text-sm font-medium text-primary">
                  Selected: {selectedContactName}
                </p>
              )}
            </section>

            {/* Teach alias */}
            <section aria-label="Alias teaching">
              <label className="flex cursor-pointer items-center gap-2.5">
                <input
                  type="checkbox"
                  checked={teachAlias}
                  onChange={(e) => setTeachAlias(e.target.checked)}
                  className="h-4 w-4 rounded border-border accent-primary"
                  aria-label="Remember this match as an alias"
                />
                <span className="text-sm font-medium text-foreground">Remember this match (teach alias)</span>
              </label>
              {teachAlias && (
                <div className="mt-2">
                  <label htmlFor="alias-text" className="sr-only">Alias text</label>
                  <input
                    id="alias-text"
                    type="text"
                    value={aliasText}
                    onChange={(e) => setAliasText(e.target.value)}
                    placeholder="Alias text"
                    className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
              )}
            </section>
          </div>

          {/* Footer actions */}
          <div className="flex items-center justify-between gap-2 border-t border-border px-5 py-4">
            <button
              type="button"
              onClick={handleNoMatch}
              disabled={submitting}
              className="flex min-h-[40px] items-center gap-1.5 rounded-xl border border-red-200 px-4 py-2 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              No Match
            </button>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onClose}
                disabled={submitting}
                className="flex min-h-[40px] items-center rounded-xl border border-border px-4 py-2 text-sm font-semibold text-foreground hover:bg-muted disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleMatch}
                disabled={!selectedContactId || submitting}
                className="flex min-h-[40px] items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
              >
                {submitting ? (
                  <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                ) : (
                  <Check size={14} aria-hidden="true" />
                )}
                Match
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
