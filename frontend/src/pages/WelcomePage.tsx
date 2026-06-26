/**
 * WelcomePage — /welcome (PUBLIC — no auth, no BottomNav)
 *
 * Renders the public newcomer intake form driven by the is_public profile
 * schema from GET /public/newcomer/profile. On successful submit shows
 * the profile's success_message; redirects if redirect_after_submit is set.
 *
 * 429 → user-friendly rate-limit message.
 * Other API errors → sonner toast.
 */

import { useState } from 'react';
import { toast } from 'sonner';
import { CheckCircle } from 'lucide-react';

import { usePublicNewcomerProfile } from '@/hooks/useProfiles';
import { publicNewcomerApi } from '@/services/profilesApi';
import { ProfileFormRenderer } from '@/components/profiles/ProfileFormRenderer';
import { LoadingState, ErrorState } from '@/components/ui/StateViews';
import type { NewcomerSubmission } from '@/types/profile';

// ---------- Page --------------------------------------------------------------

export function WelcomePage() {
  const { data, isLoading, isError, refetch } = usePublicNewcomerProfile();
  const [submitted, setSubmitted] = useState(false);
  const [successMessage, setSuccessMessage] = useState('');
  const [rateLimited, setRateLimited] = useState(false);

  const handleSubmit = async (values: Record<string, unknown>) => {
    setRateLimited(false);
    try {
      const result = await publicNewcomerApi.submit(values as NewcomerSubmission);

      if (result.status === 'duplicate') {
        toast.info(result.message || 'Your information has already been recorded.');
      }

      setSuccessMessage(data?.settings.success_message ?? 'Thank you!');
      setSubmitted(true);

      if (data?.settings.redirect_after_submit) {
        const redirectUrl = data.settings.redirect_after_submit;
        setTimeout(() => {
          window.location.href = redirectUrl;
        }, 3000);
      }
    } catch (err: unknown) {
      const axiosErr = err as {
        response?: { status?: number; data?: { detail?: string } };
      };

      if (axiosErr.response?.status === 429) {
        setRateLimited(true);
        toast.error('Too many submissions. Please try again in a minute.');
      } else {
        toast.error(
          axiosErr.response?.data?.detail ?? 'Submission failed',
        );
      }
    }
  };

  // ---- Loading ---------------------------------------------------------------

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="w-full max-w-lg px-4">
          <LoadingState message="Loading form…" />
        </div>
      </div>
    );
  }

  // ---- Error -----------------------------------------------------------------

  if (isError || !data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="w-full max-w-lg px-4">
          <ErrorState
            message="The newcomer form is not available right now."
            onRetry={() => refetch()}
          />
        </div>
      </div>
    );
  }

  // ---- Success ---------------------------------------------------------------

  if (submitted) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="w-full max-w-lg px-4">
          <div className="rounded-2xl border border-border bg-card p-8 text-center shadow-sm">
            <CheckCircle
              size={48}
              className="mx-auto mb-4 text-green-500"
              aria-hidden="true"
            />
            <p className="text-base font-medium text-foreground">
              {successMessage}
            </p>
            {data.settings.redirect_after_submit && (
              <p className="mt-2 text-xs text-foreground/50">
                Redirecting in 3 seconds…
              </p>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ---- Rate-limited notice ---------------------------------------------------

  // ---- Form ------------------------------------------------------------------

  return (
    <div className="flex min-h-screen items-center justify-center bg-background py-12 px-4">
      <div className="w-full max-w-lg">
        <div className="rounded-2xl border border-border bg-card p-6 shadow-sm sm:p-8">
          {/* Heading */}
          <div className="mb-6 text-center">
            <h1 className="text-xl font-bold text-foreground">{data.name}</h1>
          </div>

          {/* Rate-limit banner */}
          {rateLimited && (
            <div
              role="alert"
              className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
            >
              Too many submissions. Please try again in a minute.
            </div>
          )}

          {/* Form */}
          <ProfileFormRenderer
            schema={data}
            onSubmit={handleSubmit}
            submitLabel={data.settings.submit_label}
            isPublic
          />
        </div>
      </div>
    </div>
  );
}
