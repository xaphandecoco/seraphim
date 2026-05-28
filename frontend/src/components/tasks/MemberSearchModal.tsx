import { useState, useEffect, useRef, useCallback } from 'react';
import { Search, X, User } from 'lucide-react';
import { api } from '@/services/api';
import type { Member } from '@/types';

interface MemberSearchModalProps {
  mode: 'edit' | 'add';
  onSelect: (member: Member) => void;
  onClose: () => void;
}

export function MemberSearchModal({
  mode,
  onSelect,
  onClose,
}: MemberSearchModalProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Member[]>([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const search = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([]);
      return;
    }
    setLoading(true);
    try {
      const res = await api.get('/members', { params: { search: q } });
      setResults(res.data);
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => search(query), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, search]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-end justify-center bg-[#1F2128]/40 sm:items-center"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-t-2xl bg-white p-4 shadow-xl sm:rounded-2xl border border-[#E8DDA8]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-bold text-[#1F2128]">
            {mode === 'edit' ? 'Link to Member' : 'Add as Member'}
          </h2>
          <button
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-full text-[#1F2128]/40 transition-colors hover:bg-[#FBF8F0] hover:text-[#1F2128]"
          >
            <X size={18} />
          </button>
        </div>

        <div className="relative mb-3">
          <Search
            size={16}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-[#1F2128]/40"
          />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search members..."
            className="h-11 w-full rounded-xl border border-[#E8DDA8] bg-[#FBF8F0] pl-9 pr-4 text-sm text-[#1F2128] outline-none placeholder:text-[#1F2128]/40 focus:border-[#F5D547] focus:ring-2 focus:ring-[#F5D547]/30"
          />
        </div>

        <div className="max-h-[50vh] overflow-y-auto">
          {loading ? (
            <div className="py-8 text-center text-sm text-[#1F2128]/50">
              Searching...
            </div>
          ) : results.length === 0 ? (
            <div className="py-8 text-center text-sm text-[#1F2128]/50">
              {query.trim() ? 'No members found' : 'Start typing to search'}
            </div>
          ) : (
            <ul className="space-y-1">
              {results.map((member) => (
                <li key={member.contact_id}>
                  <button
                    onClick={() => onSelect(member)}
                    className="flex w-full min-h-[44px] items-center gap-3 rounded-xl px-3 py-2 transition-colors hover:bg-[#FBF8F0]"
                  >
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#F5D547]/20">
                      <User size={14} className="text-[#F5D547]" />
                    </div>
                    <div className="text-left">
                      <p className="text-sm font-semibold text-[#1F2128]">
                        {member.display_name || `${member.first_name} ${member.last_name}`.trim()}
                      </p>
                      {member.email && (
                        <p className="text-xs text-[#1F2128]/50">
                          {member.email}
                        </p>
                      )}
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
