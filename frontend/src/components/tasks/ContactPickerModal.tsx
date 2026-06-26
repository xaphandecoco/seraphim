import { useState, useEffect, useRef, useCallback } from 'react';
import { Search, X, User } from 'lucide-react';
import { api } from '@/services/api';
import type { Member } from '@/types';

interface ContactPickerModalProps {
  mode: 'edit' | 'add';
  onSelect: (member: Member) => void;
  onClose: () => void;
}

export function ContactPickerModal({ mode, onSelect, onClose }: ContactPickerModalProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Member[]>([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => { inputRef.current?.focus(); }, []);

  // Esc to close
  useEffect(() => {
    const handle = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handle);
    return () => document.removeEventListener('keydown', handle);
  }, [onClose]);

  // Focus trap
  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    const trap = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      const focusable = panel.querySelectorAll<HTMLElement>(
        'button, input, [tabindex]:not([tabindex="-1"])'
      );
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey ? document.activeElement === first : document.activeElement === last) {
        e.preventDefault();
        (e.shiftKey ? last : first)?.focus();
      }
    };
    panel.addEventListener('keydown', trap);
    return () => panel.removeEventListener('keydown', trap);
  }, []);

  const search = useCallback(async (q: string) => {
    if (!q.trim()) { setResults([]); return; }
    setLoading(true);
    try {
      const res = await api.get('/members', { params: { search: q } });
      // Forward-compat with S03 paginated shape {items, total, page, page_size} (C16).
      // Until S03 lands the endpoint returns a bare list; read defensively.
      setResults(Array.isArray(res.data) ? res.data : (res.data?.items ?? []));
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => search(query), 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query, search]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="member-modal-title"
      className="fixed inset-0 z-[60] flex items-end justify-center bg-foreground/40 sm:items-center"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        className="w-full max-w-md rounded-t-2xl border border-border bg-card p-4 shadow-xl sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 id="member-modal-title" className="text-lg font-bold text-foreground">
            {mode === 'edit' ? 'Link to Member' : 'Add as Member'}
          </h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="flex h-8 w-8 items-center justify-center rounded-full text-foreground/40 transition-colors hover:bg-background hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        <div className="relative mb-3">
          <label htmlFor="member-search" className="sr-only">Search members</label>
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-foreground/40" aria-hidden="true" />
          <input
            id="member-search"
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search members…"
            autoComplete="off"
            className="h-11 w-full rounded-xl border border-border bg-background pl-9 pr-4 text-sm text-foreground outline-none placeholder:text-foreground/40 focus:border-primary focus:ring-2 focus:ring-primary/30"
          />
        </div>

        <div className="max-h-[50vh] overflow-y-auto">
          {loading ? (
            <div aria-live="polite" className="py-8 text-center text-sm text-foreground/50">Searching…</div>
          ) : results.length === 0 ? (
            <div aria-live="polite" className="py-8 text-center text-sm text-foreground/50">
              {query.trim() ? 'No members found' : 'Start typing to search'}
            </div>
          ) : (
            <ul className="space-y-1">
              {results.map((member) => (
                <li key={member.contact_id}>
                  <button
                    onClick={() => onSelect(member)}
                    className="flex w-full min-h-[44px] items-center gap-3 rounded-xl px-3 py-2 transition-colors hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring"
                  >
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/20">
                      <User size={14} className="text-primary" aria-hidden="true" />
                    </div>
                    <div className="text-left">
                      <p className="text-sm font-semibold text-foreground">
                        {member.display_name || `${member.first_name} ${member.last_name}`.trim()}
                      </p>
                      {member.email && <p className="text-xs text-foreground/50">{member.email}</p>}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
