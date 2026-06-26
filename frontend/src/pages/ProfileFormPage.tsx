/**
 * ProfileFormPage — /profiles/new and /profiles/:id/edit (AdminRoute)
 *
 * Admin builder: two-panel layout (left = live preview, right = editor).
 * On save: POST /profiles (new) or PUT /profiles/:id (edit).
 */

import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import type { AxiosError } from 'axios';
import { ArrowLeft, Save } from 'lucide-react';

import { useProfile, useCreateProfile, useUpdateProfile } from '@/hooks/useProfiles';
import { ProfileFormRenderer } from '@/components/profiles/ProfileFormRenderer';
import { ProfileFieldEditor } from '@/components/profiles/ProfileFieldEditor';
import { FormField, inputClass } from '@/components/ui/FormField';
import { LoadingState, ErrorState } from '@/components/ui/StateViews';

import type {
  ProfileFieldDescriptor,
  ProfileSettings,
  ProfileRenderResponse,
  ResolvedField,
} from '@/types/profile';

interface ApiError {
  detail?: string;
}

// ---------- Helpers -----------------------------------------------------------

/** Convert a ProfileFieldDescriptor to a minimal ResolvedField for preview. */
function descriptorToResolved(desc: ProfileFieldDescriptor): ResolvedField {
  const coreLabels: Record<string, string> = {
    first_name: 'First Name',
    last_name: 'Last Name',
    phone: 'Phone',
    email: 'Email',
    gender: 'Gender',
    birth_date: 'Birth Date',
    street_address: 'Street Address',
    contact_subtype: 'Contact Subtype',
  };

  const label =
    desc.label_override ??
    (desc.core_field ? (coreLabels[desc.core_field] ?? desc.core_field) : null) ??
    desc.custom_field_name ??
    desc.id;

  return {
    id: desc.id,
    field_type: desc.field_type,
    label,
    placeholder: desc.placeholder ?? null,
    is_required: desc.is_required,
    weight: desc.weight,
    section: desc.section,
    core_field: desc.core_field,
    custom_field_name: desc.custom_field_name,
    default_value: desc.default_value,
    // data_type is resolved by backend; set text as fallback for preview
    data_type: desc.field_type === 'core'
      ? (desc.core_field === 'gender'
        ? 'select_gender'
        : desc.core_field === 'birth_date'
        ? 'date'
        : 'text')
      : 'text',
  };
}

// ---------- Page --------------------------------------------------------------

export function ProfileFormPage() {
  const { id } = useParams<{ id?: string }>();
  const profileId = id ? Number(id) : undefined;
  const isEditing = profileId != null;
  const navigate = useNavigate();

  // Fetch existing profile when editing
  const { data: existing, isLoading: loadingExisting, isError: errorExisting } = useProfile(
    isEditing ? profileId : undefined,
  );

  const createMut = useCreateProfile();
  const updateMut = useUpdateProfile(profileId ?? 0);

  // ---- Form state -----------------------------------------------------------

  const [name, setName] = useState('');
  const [entity] = useState('contact');
  const [isPublic, setIsPublic] = useState(false);
  const [fields, setFields] = useState<ProfileFieldDescriptor[]>([]);
  const [settings, setSettings] = useState<ProfileSettings>({
    submit_label: 'Submit',
    success_message: 'Thank you! Your information has been recorded.',
    notify_google_chat: true,
    notify_gmail: true,
  });
  const [nameError, setNameError] = useState('');
  const [initialised, setInitialised] = useState(false);

  // Populate form when existing profile loads
  useEffect(() => {
    if (existing && !initialised) {
      setName(existing.name);
      setIsPublic(existing.is_public);
      setFields(existing.fields);
      setSettings(existing.settings);
      setInitialised(true);
    }
  }, [existing, initialised]);

  // Reset when switching to new profile
  useEffect(() => {
    if (!isEditing) {
      setName('');
      setIsPublic(false);
      setFields([]);
      setSettings({
        submit_label: 'Submit',
        success_message: 'Thank you! Your information has been recorded.',
        notify_google_chat: true,
        notify_gmail: true,
      });
      setInitialised(false);
    }
  }, [isEditing]);

  // ---- Preview schema -------------------------------------------------------

  const previewSchema: ProfileRenderResponse = {
    profile_id: profileId ?? 0,
    name: name || 'Untitled',
    fields: fields.map(descriptorToResolved),
    settings,
  };

  // ---- Save handler ---------------------------------------------------------

  const isSaving = createMut.isPending || updateMut.isPending;

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setNameError('Profile name is required');
      return;
    }
    setNameError('');

    const body = { name: name.trim(), entity, fields, settings, is_public: isPublic };

    const mutOpts = {
      onSuccess: () => {
        toast.success(isEditing ? 'Profile updated' : 'Profile created');
        navigate('/profiles');
      },
      onError: (err: Error) => {
        const axiosErr = err as AxiosError<ApiError>;
        toast.error(axiosErr.response?.data?.detail ?? 'Failed to save profile');
      },
    };

    if (isEditing) {
      updateMut.mutate(body, mutOpts);
    } else {
      createMut.mutate(body, mutOpts);
    }
  };

  // ---- Loading/Error --------------------------------------------------------

  if (isEditing && loadingExisting) {
    return (
      <div className="flex h-screen flex-col">
        <Header isEditing={isEditing} isSaving={false} onBack={() => navigate('/profiles')} onSave={() => {}} />
        <LoadingState message="Loading profile…" />
      </div>
    );
  }

  if (isEditing && errorExisting) {
    return (
      <div className="flex h-screen flex-col">
        <Header isEditing={isEditing} isSaving={false} onBack={() => navigate('/profiles')} onSave={() => {}} />
        <ErrorState message="Failed to load profile" onRetry={() => window.location.reload()} />
      </div>
    );
  }

  // ---- Render ---------------------------------------------------------------

  return (
    <div className="flex h-screen flex-col">
      <Header
        isEditing={isEditing}
        isSaving={isSaving}
        onBack={() => navigate('/profiles')}
        onSave={handleSave}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* Left panel — live preview */}
        <aside className="hidden w-1/2 flex-col border-r border-border bg-background lg:flex overflow-y-auto">
          <div className="px-6 py-4">
            <p className="mb-4 text-xs font-bold uppercase tracking-wide text-foreground/40">
              Preview
            </p>
            {fields.length === 0 ? (
              <p className="text-sm text-foreground/40">
                Add fields on the right to see a preview.
              </p>
            ) : (
              <div className="rounded-2xl border border-border bg-card p-6 shadow-sm">
                <h2 className="mb-4 text-lg font-bold text-foreground">
                  {name || 'Untitled Profile'}
                </h2>
                <ProfileFormRenderer
                  schema={previewSchema}
                  onSubmit={async () => {
                    /* preview only — no submit */
                  }}
                  submitLabel={settings.submit_label || 'Submit'}
                />
              </div>
            )}
          </div>
        </aside>

        {/* Right panel — editor */}
        <main className="flex-1 overflow-y-auto px-4 py-4 lg:px-6">
          <form id="profile-builder-form" onSubmit={handleSave} className="space-y-6">
            {/* Profile meta */}
            <section className="space-y-4 rounded-2xl border border-border bg-card p-4">
              <h2 className="text-sm font-bold text-foreground">Profile details</h2>

              <FormField label="Profile name" htmlFor="profile-name" required error={nameError}>
                <input
                  id="profile-name"
                  type="text"
                  value={name}
                  onChange={(e) => { setName(e.target.value); if (nameError) setNameError(''); }}
                  placeholder="e.g. New Friend"
                  className={inputClass}
                />
              </FormField>

              <label className="flex items-center gap-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={isPublic}
                  onChange={(e) => setIsPublic(e.target.checked)}
                  className="rounded border-border accent-primary"
                />
                Public profile (accessible via /welcome)
              </label>
            </section>

            {/* Settings */}
            <section className="space-y-4 rounded-2xl border border-border bg-card p-4">
              <h2 className="text-sm font-bold text-foreground">Form settings</h2>

              <FormField label="Submit button label" htmlFor="submit-label">
                <input
                  id="submit-label"
                  type="text"
                  value={settings.submit_label}
                  onChange={(e) =>
                    setSettings((s) => ({ ...s, submit_label: e.target.value }))
                  }
                  placeholder="Submit"
                  className={inputClass}
                />
              </FormField>

              <FormField label="Success message" htmlFor="success-message">
                <textarea
                  id="success-message"
                  value={settings.success_message}
                  onChange={(e) =>
                    setSettings((s) => ({ ...s, success_message: e.target.value }))
                  }
                  rows={2}
                  placeholder="Thank you! Your information has been recorded."
                  className={inputClass}
                />
              </FormField>

              <FormField label="Redirect URL after submit" htmlFor="redirect-url" hint="Optional — leave blank to stay on the success screen">
                <input
                  id="redirect-url"
                  type="url"
                  value={settings.redirect_after_submit ?? ''}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      redirect_after_submit: e.target.value || null,
                    }))
                  }
                  placeholder="https://example.com/thank-you"
                  className={inputClass}
                />
              </FormField>

              <div className="flex flex-wrap gap-4 text-sm text-foreground">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={settings.notify_google_chat}
                    onChange={(e) =>
                      setSettings((s) => ({ ...s, notify_google_chat: e.target.checked }))
                    }
                    className="rounded border-border accent-primary"
                  />
                  Notify Google Chat
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={settings.notify_gmail}
                    onChange={(e) =>
                      setSettings((s) => ({ ...s, notify_gmail: e.target.checked }))
                    }
                    className="rounded border-border accent-primary"
                  />
                  Notify Gmail
                </label>
              </div>
            </section>

            {/* Field editor */}
            <section className="rounded-2xl border border-border bg-card p-4 space-y-3">
              <h2 className="text-sm font-bold text-foreground">Fields</h2>
              <ProfileFieldEditor fields={fields} onChange={setFields} />
            </section>
          </form>
        </main>
      </div>
    </div>
  );
}

// ---------- Sub-components ----------------------------------------------------

function Header({
  isEditing,
  isSaving,
  onBack,
  onSave,
}: {
  isEditing: boolean;
  isSaving: boolean;
  onBack: () => void;
  onSave: (e: React.FormEvent) => void;
}) {
  return (
    <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onBack}
          aria-label="Back to profiles"
          className="flex items-center gap-1 rounded-lg p-1.5 text-foreground/50 hover:bg-background hover:text-foreground"
        >
          <ArrowLeft size={18} aria-hidden="true" />
        </button>
        <h1 className="flex-1 text-lg font-bold text-foreground">
          {isEditing ? 'Edit Profile' : 'New Profile'}
        </h1>
        <button
          type="button"
          form="profile-builder-form"
          onClick={onSave}
          disabled={isSaving}
          className="flex items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98] focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-60"
        >
          <Save size={14} aria-hidden="true" />
          {isSaving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </header>
  );
}
