// S05-F12: BulkParticipantPanel — audience selection + bulk add/set-status/remove

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ChevronDown, ChevronUp, Users } from 'lucide-react';

import { api } from '@/services/api';
import { useAuthStore } from '@/store/authStore';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { LoadingState } from '@/components/ui/StateViews';

import type { AudienceMode, AudienceSelector, BulkParticipantPreview, BulkParticipantResult } from '@/types/bulk';

// ---------- Constants ---------------------------------------------------------

const PARTICIPANT_STATUSES = ['registered', 'attended', 'absent', 'cancelled'] as const;
type ParticipantStatus = typeof PARTICIPANT_STATUSES[number];

type OperationTab = 'add' | 'set_status' | 'remove';

// ---------- Props -------------------------------------------------------------

export interface BulkParticipantPanelProps {
  eventId: number;
  /** Contact IDs currently selected in the participant grid */
  selectedIds?: number[];
}

// ---------- API helpers -------------------------------------------------------

function buildAudience(
  mode: AudienceMode,
  selectedIds: number[],
  groupId?: number,
  savedSearchId?: number,
): AudienceSelector {
  switch (mode) {
    case 'ids':
      return { mode, contact_ids: selectedIds };
    case 'group':
      return { mode, group_id: groupId };
    case 'saved_search':
      return { mode, saved_search_id: savedSearchId };
    default:
      return { mode: 'all' };
  }
}

// ---------- Component ---------------------------------------------------------

export function BulkParticipantPanel({ eventId, selectedIds = [] }: BulkParticipantPanelProps) {
  const queryClient = useQueryClient();
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const role = useAuthStore((s) => s.user?.role);

  const [expanded, setExpanded] = useState(false);
  const [audienceMode, setAudienceMode] = useState<AudienceMode>('all');
  const [operation, setOperation] = useState<OperationTab>('add');
  const [statusValue, setStatusValue] = useState<ParticipantStatus>('attended');
  const [hardDelete, setHardDelete] = useState(false);

  const [preview, setPreview] = useState<BulkParticipantPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [applying, setApplying] = useState(false);

  // ---------- Groups query (S09 not shipped — graceful disable) ----------------

  const { isError: groupsError } = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.get('/groups').then((r) => r.data as unknown[]),
    retry: false,
    staleTime: 60_000,
  });

  // S15 gate: viewers have no bulk access (no viewer users pre-S15, but gate defensively)
  if ((role as string) === 'viewer') return null;

  // ---------- Audience selector validation ------------------------------------

  const isAudienceValid =
    audienceMode === 'all' ||
    audienceMode === 'ids' ||
    (audienceMode === 'group' && !groupsError) ||
    (audienceMode === 'saved_search' && !groupsError);

  const audience = buildAudience(audienceMode, selectedIds);

  // ---------- Preview handler --------------------------------------------------

  async function handlePreview() {
    if (!isAudienceValid) return;
    setPreviewing(true);
    setPreview(null);
    try {
      const res = await api.post<BulkParticipantPreview>(
        `/events/${eventId}/participants/bulk-preview`,
        { audience, operation },
      );
      setPreview(res.data);
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(detail ?? 'Preview failed');
    } finally {
      setPreviewing(false);
    }
  }

  // ---------- Apply handler (opens confirm dialog first) -----------------------

  function handleApplyClick() {
    if (!isAudienceValid) return;
    setConfirmOpen(true);
  }

  async function handleApplyConfirm() {
    setConfirmOpen(false);
    setApplying(true);
    try {
      let endpoint: string;
      let body: Record<string, unknown>;

      switch (operation) {
        case 'add':
          endpoint = `/events/${eventId}/participants/bulk-add`;
          body = { audience };
          break;
        case 'set_status':
          endpoint = `/events/${eventId}/participants/bulk-set-status`;
          body = { audience, status: statusValue };
          break;
        case 'remove':
          endpoint = `/events/${eventId}/participants/bulk-remove`;
          body = { audience, hard_delete: isAdmin && hardDelete };
          break;
      }

      const res = await api.post<BulkParticipantResult>(endpoint!, body!);
      const { affected, skipped, errors } = res.data;
      toast.success(
        `Done — ${affected} affected, ${skipped} skipped${errors ? `, ${errors} errors` : ''}`,
      );
      queryClient.invalidateQueries({ queryKey: ['participants', eventId] });
      queryClient.invalidateQueries({ queryKey: ['event-participants', eventId] });
      setPreview(null);
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(detail ?? 'Bulk operation failed');
    } finally {
      setApplying(false);
    }
  }

  // ---------- Confirm message --------------------------------------------------

  function confirmMessage(): string {
    const audienceLabel =
      audienceMode === 'all'
        ? 'all participants'
        : audienceMode === 'ids'
        ? `${selectedIds.length} selected contact(s)`
        : audienceMode === 'group'
        ? 'the selected group'
        : 'the saved search results';

    switch (operation) {
      case 'add':
        return `Add ${audienceLabel} to this event?`;
      case 'set_status':
        return `Set status to "${statusValue}" for ${audienceLabel}?`;
      case 'remove':
        return `Remove ${audienceLabel} from this event?${hardDelete && isAdmin ? ' This is a hard delete and cannot be undone.' : ''}`;
    }
  }

  // ---------- Render -----------------------------------------------------------

  return (
    <section
      aria-label="Bulk participant operations"
      className="rounded-2xl border border-border bg-card"
    >
      {/* Collapsible header */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between px-4 py-3 text-sm font-semibold text-foreground hover:bg-primary/5 focus:outline-none focus:ring-2 focus:ring-ring rounded-2xl"
      >
        <span className="flex items-center gap-2">
          <Users size={15} aria-hidden="true" />
          Bulk Participant Operations
        </span>
        {expanded ? (
          <ChevronUp size={15} aria-hidden="true" />
        ) : (
          <ChevronDown size={15} aria-hidden="true" />
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-4">
          {/* ---- Audience mode radio grid ---- */}
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-foreground/50">
              Audience
            </p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {(
                [
                  { value: 'all', label: 'All participants' },
                  { value: 'ids', label: `Selected (${selectedIds.length})` },
                  { value: 'group', label: 'Group', disabled: groupsError },
                  { value: 'saved_search', label: 'Saved search', disabled: groupsError },
                ] as { value: AudienceMode; label: string; disabled?: boolean | unknown }[]
              ).map(({ value, label, disabled }) => {
                const isDisabled = !!disabled || (value === 'ids' && selectedIds.length === 0);
                return (
                  <label
                    key={value}
                    title={
                      disabled
                        ? 'Groups module not available (S09 not shipped)'
                        : value === 'ids' && selectedIds.length === 0
                        ? 'Select rows in the grid first'
                        : undefined
                    }
                    className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-xs font-medium transition-colors ${
                      audienceMode === value
                        ? 'border-primary bg-primary/10 text-primary'
                        : isDisabled
                        ? 'cursor-not-allowed border-border bg-background text-foreground/30'
                        : 'border-border bg-background text-foreground hover:bg-primary/5'
                    }`}
                  >
                    <input
                      type="radio"
                      name="bulk-audience"
                      value={value}
                      checked={audienceMode === value}
                      disabled={isDisabled}
                      onChange={() => setAudienceMode(value)}
                      className="sr-only"
                    />
                    {label}
                  </label>
                );
              })}
            </div>
          </div>

          {/* ---- Operation tabs ---- */}
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-foreground/50">
              Operation
            </p>
            <div className="flex rounded-xl border border-border overflow-hidden">
              {(
                [
                  { value: 'add', label: 'Add' },
                  { value: 'set_status', label: 'Set status' },
                  { value: 'remove', label: 'Remove' },
                ] as { value: OperationTab; label: string }[]
              ).map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={operation === value}
                  onClick={() => setOperation(value)}
                  className={`flex-1 py-2 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
                    operation === value
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-background text-foreground hover:bg-primary/10'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* ---- Operation-specific options ---- */}
          {operation === 'set_status' && (
            <div>
              <label className="mb-1 block text-xs font-semibold text-foreground/60">
                New status
              </label>
              <select
                value={statusValue}
                onChange={(e) => setStatusValue(e.target.value as ParticipantStatus)}
                aria-label="Bulk status value"
                className="rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              >
                {PARTICIPANT_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s.charAt(0).toUpperCase() + s.slice(1)}
                  </option>
                ))}
              </select>
            </div>
          )}

          {operation === 'remove' && isAdmin && (
            <label className="flex items-center gap-2 text-xs font-medium text-foreground/70">
              <input
                type="checkbox"
                checked={hardDelete}
                onChange={(e) => setHardDelete(e.target.checked)}
                className="rounded border border-border"
              />
              Hard delete (permanent, no soft-delete)
            </label>
          )}

          {/* ---- Preview result ---- */}
          {previewing && <LoadingState message="Previewing…" />}
          {preview && !previewing && (
            <div
              role="status"
              aria-live="polite"
              className="rounded-xl border border-border bg-background px-4 py-3 text-sm text-foreground space-y-1"
            >
              <p className="font-semibold">Preview</p>
              <p className="text-xs text-foreground/70">
                Matched: <strong>{preview.matched}</strong>
                {preview.already_registered != null && (
                  <> · Already registered: <strong>{preview.already_registered}</strong></>
                )}
                {preview.will_add != null && (
                  <> · Will add: <strong>{preview.will_add}</strong></>
                )}
                {preview.will_remove != null && (
                  <> · Will remove: <strong>{preview.will_remove}</strong></>
                )}
                {preview.will_update != null && (
                  <> · Will update: <strong>{preview.will_update}</strong></>
                )}
              </p>
            </div>
          )}

          {/* ---- Action buttons ---- */}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handlePreview}
              disabled={previewing || applying || !isAudienceValid}
              className="flex min-h-[36px] items-center gap-1.5 rounded-xl border border-border bg-background px-4 py-1.5 text-xs font-semibold text-foreground hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            >
              Preview
            </button>
            <button
              type="button"
              onClick={handleApplyClick}
              disabled={applying || !isAudienceValid}
              className={`flex min-h-[36px] items-center gap-1.5 rounded-xl px-4 py-1.5 text-xs font-semibold text-primary-foreground shadow-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50 ${
                operation === 'remove'
                  ? 'bg-red-500 hover:bg-red-600'
                  : 'bg-primary hover:bg-primary/85'
              }`}
            >
              {applying ? 'Applying…' : 'Apply'}
            </button>
          </div>
        </div>
      )}

      {/* Confirm dialog */}
      {confirmOpen && (
        <ConfirmDialog
          message={confirmMessage()}
          confirmLabel={operation === 'remove' ? 'Remove' : 'Apply'}
          destructive={operation === 'remove'}
          onConfirm={handleApplyConfirm}
          onCancel={() => setConfirmOpen(false)}
        />
      )}
    </section>
  );
}
