// S11 — MergeModal: side-by-side merge UI for a candidate pair.

import { useState, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { X, ArrowLeftRight, AlertTriangle } from 'lucide-react';
import { mergePreview, mergeContacts } from '@/services/dedupe';
import type { CandidatePair, MergePreviewResponse, FieldConflictValue } from '@/types/dedupe';
import { LoadingState, ErrorState } from '@/components/ui/StateViews';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';

interface MergeModalProps {
  pair: CandidatePair;
  onClose: () => void;
}

function contactScore(c: { participant_count: number; face_sample_count: number }): number {
  return c.participant_count + c.face_sample_count;
}

function formatFieldValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function FieldConflictRow({
  field,
  conflict,
  choice,
  onChoose,
}: {
  field: string;
  conflict: FieldConflictValue;
  choice: 'survivor' | 'loser';
  onChoose: (src: 'survivor' | 'loser') => void;
}) {
  const label = field.replace(/_/g, ' ');
  return (
    <div className="border-b border-border py-3 last:border-0">
      <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-foreground/50">
        {label}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          aria-pressed={choice === 'survivor'}
          onClick={() => onChoose('survivor')}
          className={`flex-1 rounded-xl border px-3 py-2 text-left text-xs transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
            choice === 'survivor'
              ? 'border-primary bg-primary/10 font-semibold text-primary'
              : 'border-border bg-background text-foreground/70 hover:bg-card'
          }`}
        >
          <span className="block text-[10px] font-bold uppercase text-foreground/40 mb-0.5">Survivor</span>
          {formatFieldValue(conflict.survivor)}
        </button>
        <button
          type="button"
          aria-pressed={choice === 'loser'}
          onClick={() => onChoose('loser')}
          className={`flex-1 rounded-xl border px-3 py-2 text-left text-xs transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
            choice === 'loser'
              ? 'border-primary bg-primary/10 font-semibold text-primary'
              : 'border-border bg-background text-foreground/70 hover:bg-card'
          }`}
        >
          <span className="block text-[10px] font-bold uppercase text-foreground/40 mb-0.5">Loser</span>
          {formatFieldValue(conflict.loser)}
        </button>
      </div>
    </div>
  );
}

export function MergeModal({ pair, onClose }: MergeModalProps) {
  const queryClient = useQueryClient();

  // default survivor = contact with higher history score
  const aScore = contactScore(pair.contact_a);
  const bScore = contactScore(pair.contact_b);
  const defaultSurvivorId = aScore >= bScore ? pair.contact_a.id : pair.contact_b.id;

  const [survivorId, setSurvivorId] = useState(defaultSurvivorId);
  const loserId = survivorId === pair.contact_a.id ? pair.contact_b.id : pair.contact_a.id;
  const survivor = survivorId === pair.contact_a.id ? pair.contact_a : pair.contact_b;
  const loser = loserId === pair.contact_a.id ? pair.contact_a : pair.contact_b;

  const [preview, setPreview] = useState<MergePreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // field_choices: only track fields that differ
  const [fieldChoices, setFieldChoices] = useState<Record<string, 'survivor' | 'loser'>>({});

  // same-name confirmation checkbox
  const [sameNameConfirmed, setSameNameConfirmed] = useState(false);

  // confirm dialog gate
  const [showConfirm, setShowConfirm] = useState(false);

  // fetch preview whenever survivor/loser IDs change
  useEffect(() => {
    let cancelled = false;
    setPreviewLoading(true);
    setPreviewError(null);
    setPreview(null);
    mergePreview(survivorId, loserId)
      .then((data) => {
        if (!cancelled) {
          setPreview(data);
          // initialize choices: for differing fields, default to survivor
          const initial: Record<string, 'survivor' | 'loser'> = {};
          Object.entries(data.field_conflicts).forEach(([field, fc]) => {
            if (fc.differs) initial[field] = 'survivor';
          });
          Object.entries(data.custom_field_conflicts).forEach(([field, fc]) => {
            if (fc.differs) initial[field] = 'survivor';
          });
          setFieldChoices(initial);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const axErr = err as { response?: { data?: { detail?: string } } };
          setPreviewError(axErr.response?.data?.detail ?? 'Failed to load preview');
        }
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });
    return () => { cancelled = true; };
  }, [survivorId, loserId]);

  const mergeMutation = useMutation({
    mutationFn: () =>
      mergeContacts({
        survivor_id: survivorId,
        loser_id: loserId,
        confirm_same_name: sameNameConfirmed,
        field_choices: fieldChoices,
      }),
    onSuccess: () => {
      const survivorName = `${survivor.first_name} ${survivor.last_name}`.trim();
      toast.success(`Merged into ${survivorName}`);
      queryClient.invalidateQueries({ queryKey: ['dedupe', 'candidates'] });
      queryClient.invalidateQueries({ queryKey: ['dedupe', 'history'] });
      queryClient.invalidateQueries({ queryKey: ['contacts'] });
      queryClient.invalidateQueries({ queryKey: ['contact', survivorId] });
      onClose();
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr.response?.data?.detail ?? 'Merge failed');
    },
  });

  const sameName = preview?.same_name ?? pair.same_name;
  const confirmDisabled = sameName && !sameNameConfirmed;
  const canMerge = !confirmDisabled && !mergeMutation.isPending;

  function handleSwap() {
    setSurvivorId(loserId);
    setSameNameConfirmed(false);
  }

  const totalReassigned = preview
    ? Object.values(preview.reassignments).reduce((sum, r) => sum + r.reassigned, 0)
    : 0;
  const totalDeleted = preview
    ? Object.values(preview.reassignments).reduce((sum, r) => sum + r.deleted, 0)
    : 0;

  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center p-0 sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Merge contacts"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-foreground/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <div className="relative flex w-full max-w-2xl flex-col rounded-t-2xl border border-border bg-card shadow-xl sm:rounded-2xl max-h-[92vh]">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <h2 className="text-base font-bold text-foreground">Review &amp; Merge</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-8 w-8 items-center justify-center rounded-full text-foreground/40 hover:bg-background hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {/* Contact cards — side by side (md+) or stacked (mobile) */}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {/* Survivor */}
            <div className="rounded-xl border border-primary/40 bg-primary/5 p-4">
              <p className="mb-1 text-[10px] font-bold uppercase tracking-wide text-primary">
                Survivor (kept)
              </p>
              <p className="font-semibold text-foreground">
                {survivor.first_name} {survivor.last_name}
              </p>
              {survivor.email && (
                <p className="text-xs text-foreground/60 truncate">{survivor.email}</p>
              )}
              {survivor.phone && (
                <p className="text-xs text-foreground/60">{survivor.phone}</p>
              )}
              <p className="mt-2 text-xs text-foreground/50">
                {survivor.participant_count} events · {survivor.face_sample_count} face samples
              </p>
            </div>

            {/* Loser */}
            <div className="rounded-xl border border-border bg-background p-4">
              <p className="mb-1 text-[10px] font-bold uppercase tracking-wide text-foreground/40">
                Loser (merged away)
              </p>
              <p className="font-semibold text-foreground">
                {loser.first_name} {loser.last_name}
              </p>
              {loser.email && (
                <p className="text-xs text-foreground/60 truncate">{loser.email}</p>
              )}
              {loser.phone && (
                <p className="text-xs text-foreground/60">{loser.phone}</p>
              )}
              <p className="mt-2 text-xs text-foreground/50">
                {loser.participant_count} events · {loser.face_sample_count} face samples
              </p>
            </div>
          </div>

          {/* Swap button */}
          <div className="flex justify-center">
            <button
              type="button"
              onClick={handleSwap}
              className="flex items-center gap-2 rounded-xl border border-border bg-background px-4 py-2 text-xs font-semibold text-foreground/70 hover:bg-card hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <ArrowLeftRight size={13} aria-hidden="true" />
              Swap survivor / loser
            </button>
          </div>

          {/* Preview area */}
          {previewLoading && <LoadingState message="Loading preview…" />}
          {previewError && (
            <ErrorState
              message={previewError}
              onRetry={() => {
                setPreviewError(null);
                setSurvivorId((id) => id); // trigger re-render = re-fetch via useEffect
              }}
            />
          )}

          {preview && !previewLoading && (
            <>
              {/* Warnings */}
              {preview.warnings.length > 0 && (
                <div className="space-y-2">
                  {preview.warnings.map((w, i) => (
                    <div
                      key={i}
                      className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-xs text-destructive"
                    >
                      <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
                      {w}
                    </div>
                  ))}
                </div>
              )}

              {/* Field conflicts */}
              {Object.keys(preview.field_conflicts).length > 0 && (
                <section>
                  <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-foreground/50">
                    Field conflicts — pick a value
                  </h3>
                  <div className="rounded-xl border border-border bg-card px-4">
                    {Object.entries(preview.field_conflicts).map(([field, fc]) =>
                      fc.differs ? (
                        <FieldConflictRow
                          key={field}
                          field={field}
                          conflict={fc}
                          choice={fieldChoices[field] ?? 'survivor'}
                          onChoose={(src) =>
                            setFieldChoices((prev) => ({ ...prev, [field]: src }))
                          }
                        />
                      ) : (
                        <div key={field} className="border-b border-border py-2 last:border-0">
                          <p className="text-xs text-foreground/40">
                            <span className="font-semibold">{field.replace(/_/g, ' ')}</span>:{' '}
                            {formatFieldValue(fc.survivor)}
                          </p>
                        </div>
                      ),
                    )}
                  </div>
                </section>
              )}

              {/* Custom field conflicts */}
              {Object.keys(preview.custom_field_conflicts).length > 0 && (
                <section>
                  <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-foreground/50">
                    Custom field conflicts
                  </h3>
                  <div className="rounded-xl border border-border bg-card px-4">
                    {Object.entries(preview.custom_field_conflicts).map(([field, fc]) =>
                      fc.differs ? (
                        <FieldConflictRow
                          key={`custom:${field}`}
                          field={field}
                          conflict={fc}
                          choice={fieldChoices[`custom:${field}`] ?? 'survivor'}
                          onChoose={(src) =>
                            setFieldChoices((prev) => ({ ...prev, [`custom:${field}`]: src }))
                          }
                        />
                      ) : (
                        <div key={field} className="border-b border-border py-2 last:border-0">
                          <p className="text-xs text-foreground/40">
                            <span className="font-semibold">{field}</span>:{' '}
                            {formatFieldValue(fc.survivor)}
                          </p>
                        </div>
                      ),
                    )}
                  </div>
                </section>
              )}

              {/* Impact panel */}
              <section className="rounded-xl border border-border bg-background p-4">
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wide text-foreground/50">
                  Impact
                </h3>
                {Object.keys(preview.reassignments).length === 0 ? (
                  <p className="text-xs text-foreground/50">No records will be reassigned.</p>
                ) : (
                  <ul className="space-y-1">
                    {Object.entries(preview.reassignments).map(([table, counts]) => (
                      <li key={table} className="text-xs text-foreground/70">
                        <span className="font-semibold">{table}</span>:{' '}
                        {counts.reassigned} reassigned
                        {counts.deleted > 0 && (
                          <span className="text-destructive"> · {counts.deleted} removed</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                <div className="mt-3 flex gap-4 border-t border-border pt-3 text-xs text-foreground/60">
                  <span>{totalReassigned} records reassigned</span>
                  {totalDeleted > 0 && (
                    <span className="text-destructive">{totalDeleted} duplicates removed</span>
                  )}
                </div>
              </section>
            </>
          )}

          {/* Same-name confirmation gate */}
          {sameName && (
            <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3">
              <input
                type="checkbox"
                checked={sameNameConfirmed}
                onChange={(e) => setSameNameConfirmed(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border border-border accent-primary"
              />
              <span className="text-xs font-semibold text-destructive">
                I confirm these are the same person. Same-name merges require explicit confirmation
                (never auto-merged).
              </span>
            </label>
          )}
        </div>

        {/* Sticky footer */}
        <div className="border-t border-border px-5 py-4 flex gap-3">
          <button
            type="button"
            onClick={onClose}
            className="flex-1 rounded-xl border border-border bg-background py-2.5 text-sm font-semibold text-foreground hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!canMerge || !preview}
            onClick={() => setShowConfirm(true)}
            className="flex-1 rounded-xl bg-destructive py-2.5 text-sm font-bold text-white shadow-sm transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40 hover:bg-destructive/85 focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {mergeMutation.isPending ? 'Merging…' : 'Confirm Merge'}
          </button>
        </div>
      </div>

      {/* Final confirm dialog */}
      {showConfirm && (
        <ConfirmDialog
          message={`Merge "${loser.first_name} ${loser.last_name}" into "${survivor.first_name} ${survivor.last_name}"? This will reassign ${totalReassigned} record(s) and cannot be automatically reversed.`}
          confirmLabel="Merge"
          destructive
          onConfirm={() => {
            setShowConfirm(false);
            mergeMutation.mutate();
          }}
          onCancel={() => setShowConfirm(false)}
        />
      )}
    </div>
  );
}
