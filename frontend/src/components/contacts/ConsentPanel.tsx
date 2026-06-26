// S08 — ConsentPanel — biometric consent status & RTBF controls
// Mounts on ContactDetailPage below FacePanel.
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ShieldCheck } from 'lucide-react';
import { biometricApi } from '@/services/biometric';
import { useAuthStore } from '@/store/authStore';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { LoadingState, ErrorState } from '@/components/ui/StateViews';
import { RecordConsentDialog } from '@/components/contacts/RecordConsentDialog';
import type { ConsentResponse, ConsentStatus, PurgeResultResponse } from '@/types/biometric';
import type { AxiosError } from 'axios';

// ---------------------------------------------------------------------------
// Status pill
// ---------------------------------------------------------------------------

const STATUS_PILL: Record<
  ConsentStatus,
  { className: string; label: string }
> = {
  none: {
    className: 'bg-muted text-foreground/60',
    label: 'No Consent',
  },
  pending: {
    className:
      'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
    label: 'Pending',
  },
  given: {
    className:
      'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300',
    label: 'Consent Given',
  },
  revoked: {
    className: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
    label: 'Revoked',
  },
  purged: {
    className: 'bg-foreground/10 text-foreground/50',
    label: 'Purged',
  },
};

function StatusPill({ status }: { status: ConsentStatus }) {
  const pill = STATUS_PILL[status] ?? {
    className: 'bg-muted text-foreground/60',
    label: status,
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ${pill.className}`}
    >
      {pill.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Confirm-state machine
// ---------------------------------------------------------------------------

type ConfirmStage = null | 'revoke' | 'delete-request' | 'purge-1' | 'purge-2';

// ---------------------------------------------------------------------------
// Dialog mode: null=closed, 'record'=POST new, 'edit'=PATCH existing
// ---------------------------------------------------------------------------

type DialogMode = null | 'record' | 'edit';

// ---------------------------------------------------------------------------
// ConsentPanel
// ---------------------------------------------------------------------------

interface ConsentPanelProps {
  contactId: number;
}

export function ConsentPanel({ contactId }: ConsentPanelProps) {
  const queryClient = useQueryClient();
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const user = useAuthStore((s) => s.user);
  // volunteer+ = any authenticated user (User.role is 'volunteer'|'admin')
  const isVolunteerPlus = !!user;

  const [confirmStage, setConfirmStage] = useState<ConfirmStage>(null);
  const [dialogMode, setDialogMode] = useState<DialogMode>(null);

  // ---------- query -------------------------------------------------------

  const {
    data: consent,
    isLoading,
    isError,
    refetch,
  } = useQuery<ConsentResponse, AxiosError>({
    queryKey: ['biometric', 'consent', contactId],
    queryFn: () => biometricApi.getConsent(contactId),
  });

  // ---------- invalidation helper ----------------------------------------

  function invalidateAll() {
    queryClient.invalidateQueries({
      queryKey: ['biometric', 'consent', contactId],
    });
    // Also invalidate FacePanel so purge clears thumbnails
    queryClient.invalidateQueries({
      queryKey: ['contacts', contactId, 'faces'],
    });
  }

  // ---------- mutations ---------------------------------------------------

  const revokeMutation = useMutation<ConsentResponse, AxiosError, void>({
    mutationFn: () => biometricApi.revokeConsent(contactId),
    onSuccess: () => {
      invalidateAll();
      toast.success('Consent revoked');
      setConfirmStage(null);
    },
    onError: (err) => {
      const detail = (err.response?.data as { detail?: string } | undefined)
        ?.detail;
      toast.error(detail ?? 'Failed to revoke consent');
      setConfirmStage(null);
    },
  });

  const deleteRequestMutation = useMutation<
    ConsentResponse | PurgeResultResponse,
    AxiosError,
    void
  >({
    mutationFn: () => biometricApi.requestDeletion(contactId, {}),
    onSuccess: () => {
      invalidateAll();
      toast.success('Deletion request submitted');
      setConfirmStage(null);
    },
    onError: (err) => {
      const detail = (err.response?.data as { detail?: string } | undefined)
        ?.detail;
      toast.error(detail ?? 'Failed to submit deletion request');
      setConfirmStage(null);
    },
  });

  const purgeMutation = useMutation<PurgeResultResponse, AxiosError, void>({
    mutationFn: () => biometricApi.purge(contactId),
    onSuccess: (data) => {
      invalidateAll();
      const d = data.purge_detail;
      toast.success(
        `Purged: ${d.files_deleted} file${d.files_deleted !== 1 ? 's' : ''}, ${d.samples_deleted} sample${d.samples_deleted !== 1 ? 's' : ''}, ${d.detections_cleared} detection${d.detections_cleared !== 1 ? 's' : ''}`,
      );
      setConfirmStage(null);
    },
    onError: (err) => {
      const detail = (err.response?.data as { detail?: string } | undefined)
        ?.detail;
      toast.error(detail ?? 'Purge failed');
      setConfirmStage(null);
    },
  });

  // ---------- loading / error states -------------------------------------

  if (isLoading) {
    return (
      <section
        data-slot="consent"
        className="bg-card rounded-2xl border border-border p-4"
      >
        <h3 className="mb-3 text-sm font-semibold text-foreground">
          Biometric Consent
        </h3>
        <LoadingState message="Loading consent…" />
      </section>
    );
  }

  if (isError || !consent) {
    return (
      <section
        data-slot="consent"
        className="bg-card rounded-2xl border border-border p-4"
      >
        <h3 className="mb-3 text-sm font-semibold text-foreground">
          Biometric Consent
        </h3>
        <ErrorState
          message="Failed to load consent data"
          onRetry={() => refetch()}
        />
      </section>
    );
  }

  // ---------- role-gated capabilities ------------------------------------

  const isPurged = consent.status === 'purged';
  const canMutate = isVolunteerPlus && !isPurged;
  // Record: status is 'none' or 'revoked' → send POST
  const canRecord =
    canMutate &&
    (consent.status === 'none' || consent.status === 'revoked');
  // Edit note/retention: status is 'given' or 'pending' → send PATCH
  const canEdit =
    canMutate &&
    (consent.status === 'given' || consent.status === 'pending');
  const canRevoke = canMutate && consent.status === 'given';
  const canRequestDeletion =
    canMutate &&
    consent.status !== 'none' &&
    !consent.deletion_requested_at;
  // Purge: admin only, not already purged, and there must be a consent row
  const canPurge = isAdmin && !isPurged && consent.status !== 'none';

  // ---------- render -----------------------------------------------------

  return (
    <>
      <section
        data-slot="consent"
        className="bg-card rounded-2xl border border-border p-4 space-y-3"
      >
        {/* Header row */}
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">
            Biometric Consent
          </h3>
          <StatusPill status={consent.status} />
        </div>

        {/* Amber "consent needed" banner */}
        {consent.subject_active && consent.status === 'none' && (
          <div className="flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2.5 dark:border-amber-700 dark:bg-amber-900/20">
            <ShieldCheck
              size={16}
              className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400"
              aria-hidden="true"
            />
            <p className="text-xs font-medium text-amber-800 dark:text-amber-300">
              Consent needed — this active subject has no biometric consent on
              record.
            </p>
          </div>
        )}

        {/* Detail rows */}
        {consent.status !== 'none' && (
          <dl className="space-y-1 text-xs text-foreground/60">
            {consent.consented_at && (
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 font-medium text-foreground/60">
                  Consented
                </dt>
                <dd>
                  {new Date(consent.consented_at).toLocaleDateString()}
                </dd>
              </div>
            )}
            {consent.basis_note && (
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 font-medium text-foreground/60">
                  Basis
                </dt>
                <dd className="break-words">{consent.basis_note}</dd>
              </div>
            )}
            {consent.retention_until && (
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 font-medium text-foreground/60">
                  Retain until
                </dt>
                <dd>
                  {new Date(consent.retention_until).toLocaleDateString()}
                </dd>
              </div>
            )}
            {consent.deletion_requested_at && (
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 font-medium text-foreground/60">
                  Deletion req.
                </dt>
                <dd className="text-red-600 dark:text-red-400">
                  {new Date(
                    consent.deletion_requested_at,
                  ).toLocaleDateString()}
                </dd>
              </div>
            )}
            {consent.purged_at && (
              <div className="flex gap-2">
                <dt className="w-28 shrink-0 font-medium text-foreground/60">
                  Purged at
                </dt>
                <dd>{new Date(consent.purged_at).toLocaleDateString()}</dd>
              </div>
            )}
            <div className="flex gap-2">
              <dt className="w-28 shrink-0 font-medium text-foreground/60">
                Photos
              </dt>
              <dd>{consent.enrolled_photo_count}</dd>
            </div>
          </dl>
        )}

        {/* Mutation controls — hidden from viewers (unauthenticated) */}
        {isVolunteerPlus && (
          <div className="flex flex-wrap gap-2 pt-1">
            {canRecord && (
              <button
                type="button"
                onClick={() => setDialogMode('record')}
                className="rounded-xl border border-border bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:bg-primary/85 focus:outline-none focus:ring-2 focus:ring-ring"
              >
                Record Consent
              </button>
            )}
            {canEdit && (
              <button
                type="button"
                onClick={() => setDialogMode('edit')}
                className="rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-primary/10 focus:outline-none focus:ring-2 focus:ring-ring"
              >
                Edit
              </button>
            )}
            {canRevoke && (
              <button
                type="button"
                onClick={() => setConfirmStage('revoke')}
                className="rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-semibold text-amber-600 hover:bg-amber-50 dark:hover:bg-amber-900/20 focus:outline-none focus:ring-2 focus:ring-ring"
              >
                Revoke
              </button>
            )}
            {canRequestDeletion && (
              <button
                type="button"
                onClick={() => setConfirmStage('delete-request')}
                className="rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-semibold text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 focus:outline-none focus:ring-2 focus:ring-ring"
              >
                Request Deletion
              </button>
            )}
            {canPurge && (
              <button
                type="button"
                onClick={() => setConfirmStage('purge-1')}
                className="rounded-xl border border-red-300 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100 dark:border-red-700 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/40 focus:outline-none focus:ring-2 focus:ring-ring"
              >
                Purge Now
              </button>
            )}
          </div>
        )}
      </section>

      {/* ---------- RecordConsentDialog ---------- */}
      {dialogMode !== null && (
        <RecordConsentDialog
          contactId={contactId}
          existing={dialogMode === 'edit' ? consent : null}
          onClose={() => setDialogMode(null)}
        />
      )}

      {/* ---------- Revoke confirm ---------- */}
      {confirmStage === 'revoke' && (
        <ConfirmDialog
          message="Revoke biometric consent? The contact's face data will remain but no further biometric processing will occur until consent is re-recorded."
          confirmLabel="Revoke Consent"
          destructive
          onConfirm={() => revokeMutation.mutate()}
          onCancel={() => setConfirmStage(null)}
        />
      )}

      {/* ---------- Deletion request confirm ---------- */}
      {confirmStage === 'delete-request' && (
        <ConfirmDialog
          message="Submit a deletion request? An admin will review and schedule the biometric data purge."
          confirmLabel="Request Deletion"
          destructive
          onConfirm={() => deleteRequestMutation.mutate()}
          onCancel={() => setConfirmStage(null)}
        />
      )}

      {/* ---------- Purge — first confirm ---------- */}
      {confirmStage === 'purge-1' && (
        <ConfirmDialog
          message="Permanently purge all biometric data for this contact? All face samples, files, and CompreFace entries will be irreversibly deleted."
          confirmLabel="Yes, Continue"
          destructive
          onConfirm={() => setConfirmStage('purge-2')}
          onCancel={() => setConfirmStage(null)}
        />
      )}

      {/* ---------- Purge — second confirm (FINAL WARNING) ---------- */}
      {confirmStage === 'purge-2' && (
        <ConfirmDialog
          message="FINAL WARNING: This action cannot be undone. All face samples, enrolled photos, and CompreFace entries for this contact will be permanently and irreversibly deleted. Are you absolutely sure?"
          confirmLabel="Purge Permanently"
          destructive
          onConfirm={() => purgeMutation.mutate()}
          onCancel={() => setConfirmStage(null)}
        />
      )}
    </>
  );
}
