// S08 — RecordConsentDialog — record (POST) or update (PATCH) biometric consent
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { X } from 'lucide-react';
import { biometricApi } from '@/services/biometric';
import { useAuthStore } from '@/store/authStore';
import type { ConsentResponse } from '@/types/biometric';
import type { AxiosError } from 'axios';

interface RecordConsentDialogProps {
  contactId: number;
  /** null = record new consent (POST); non-null = edit existing (PATCH) */
  existing: ConsentResponse | null;
  onClose: () => void;
}

export function RecordConsentDialog({
  contactId,
  existing,
  onClose,
}: RecordConsentDialogProps) {
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const queryClient = useQueryClient();

  const isNew = existing === null;

  const [basisNote, setBasisNote] = useState(existing?.basis_note ?? '');
  const [retentionYears, setRetentionYears] = useState('');
  const [retentionUntil, setRetentionUntil] = useState(
    existing?.retention_until ? existing.retention_until.slice(0, 10) : '',
  );

  const mutation = useMutation<ConsentResponse, AxiosError, void>({
    mutationFn: () => {
      if (isNew) {
        return biometricApi.recordConsent(contactId, {
          ...(basisNote ? { basis_note: basisNote } : {}),
          ...(isAdmin && retentionYears
            ? { retention_years: Number(retentionYears) }
            : {}),
        });
      }
      return biometricApi.updateConsent(contactId, {
        ...(basisNote ? { basis_note: basisNote } : {}),
        ...(isAdmin && retentionUntil ? { retention_until: retentionUntil } : {}),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['biometric', 'consent', contactId],
      });
      toast.success(isNew ? 'Consent recorded' : 'Consent updated');
      onClose();
    },
    onError: (err) => {
      const detail = (err.response?.data as { detail?: string } | undefined)
        ?.detail;
      toast.error(
        detail ?? (isNew ? 'Failed to record consent' : 'Failed to update consent'),
      );
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center p-4 sm:items-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-foreground/30 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Dialog panel */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={isNew ? 'Record Consent' : 'Update Consent'}
        className="relative w-full max-w-sm rounded-2xl border border-border bg-card p-5 shadow-xl"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-bold text-foreground">
            {isNew ? 'Record Consent' : 'Update Consent'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="flex h-8 w-8 items-center justify-center rounded-lg text-foreground/40 hover:bg-muted hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="space-y-4">
          {/* Basis / notes (all roles) */}
          <div>
            <label
              htmlFor="consent-basis-note"
              className="mb-1.5 block text-xs font-medium text-foreground/60"
            >
              Basis / Notes{' '}
              <span className="font-normal text-foreground/40">(optional)</span>
            </label>
            <textarea
              id="consent-basis-note"
              value={basisNote}
              onChange={(e) => setBasisNote(e.target.value)}
              rows={3}
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-foreground/30 focus:outline-none focus:ring-2 focus:ring-ring resize-none"
              placeholder="e.g. Verbal consent given at Sunday service"
            />
          </div>

          {/* Retention years — new record, admin only */}
          {isAdmin && isNew && (
            <div>
              <label
                htmlFor="consent-retention-years"
                className="mb-1.5 block text-xs font-medium text-foreground/60"
              >
                Retention (years){' '}
                <span className="font-normal text-foreground/40">
                  (optional, admin)
                </span>
              </label>
              <input
                id="consent-retention-years"
                type="number"
                min="1"
                max="20"
                value={retentionYears}
                onChange={(e) => setRetentionYears(e.target.value)}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-foreground/30 focus:outline-none focus:ring-2 focus:ring-ring"
                placeholder="e.g. 3"
              />
              <p className="mt-1 text-xs text-foreground/40">
                Leave blank to use the system default retention period.
              </p>
            </div>
          )}

          {/* Retention until — update only, admin only */}
          {isAdmin && !isNew && (
            <div>
              <label
                htmlFor="consent-retention-until"
                className="mb-1.5 block text-xs font-medium text-foreground/60"
              >
                Retain Until{' '}
                <span className="font-normal text-foreground/40">
                  (optional, admin)
                </span>
              </label>
              <input
                id="consent-retention-until"
                type="date"
                value={retentionUntil}
                onChange={(e) => setRetentionUntil(e.target.value)}
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="mt-5 flex gap-2">
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 flex-1 items-center justify-center rounded-xl border border-border bg-background text-sm font-semibold text-foreground transition-colors hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="flex h-10 flex-1 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {mutation.isPending ? 'Saving…' : isNew ? 'Record' : 'Update'}
          </button>
        </div>
      </div>
    </div>
  );
}
