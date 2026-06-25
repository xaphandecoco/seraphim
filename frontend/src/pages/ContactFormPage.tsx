import { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQueryClient, useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft } from 'lucide-react';
import type { AxiosError } from 'axios';

import { useContact } from '@/hooks/useContact';
import { useCustomFieldSchema } from '@/hooks/useCustomFieldSchema';
import { contactsApi, type ContactCreate, type ContactUpdate } from '@/services/contacts';
import { FormField, inputClass } from '@/components/ui/FormField';
import { CustomFieldsSection } from '@/components/customFields/CustomFieldsSection';
import { LoadingState } from '@/components/ui/StateViews';

import type { ContactDetail } from '@/types';
import type { CustomDataValue } from '@/types/customFields';

// ---------- Types ------------------------------------------------------------

interface Props {
  mode: 'create' | 'edit';
}

interface FormState {
  first_name: string;
  last_name: string;
  nickname: string;
  suffix: string;
  contact_type: string;
  contact_subtype: string;
  gender: string;
  birth_date: string;
  phone: string;
  email: string;
  street_address: string;
}

interface ApiError422Detail {
  field: string;
  error: string;
}

// ---------- Helper -----------------------------------------------------------

function emptyForm(): FormState {
  return {
    first_name: '',
    last_name: '',
    nickname: '',
    suffix: '',
    contact_type: 'individual',
    contact_subtype: '',
    gender: '',
    birth_date: '',
    phone: '',
    email: '',
    street_address: '',
  };
}

function seedForm(contact: ContactDetail): FormState {
  return {
    first_name: contact.first_name ?? '',
    last_name: contact.last_name ?? '',
    nickname: contact.nickname ?? '',
    suffix: contact.suffix ?? '',
    contact_type: contact.contact_type ?? 'individual',
    contact_subtype: contact.contact_subtype ?? '',
    gender: contact.gender ?? '',
    birth_date: contact.birth_date ?? '',
    phone: contact.phone ?? '',
    email: contact.email ?? '',
    street_address: contact.street_address ?? '',
  };
}

// ---------- Main component ---------------------------------------------------

export function ContactFormPage({ mode }: Props) {
  const { id: idParam } = useParams<{ id: string }>();
  const id = idParam ? Number(idParam) : undefined;
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [form, setForm] = useState<FormState>(emptyForm());
  const [customData, setCustomData] = useState<Record<string, CustomDataValue>>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // Edit mode: prefetch contact
  const {
    data: existingContact,
    isLoading: contactLoading,
  } = useContact(mode === 'edit' ? id : undefined);

  // Seed form when contact loads in edit mode
  useEffect(() => {
    if (mode === 'edit' && existingContact) {
      setForm(seedForm(existingContact));
      if (existingContact.custom_data) {
        setCustomData(existingContact.custom_data as Record<string, CustomDataValue>);
      }
    }
  }, [mode, existingContact]);

  // Custom field schema
  const { data: schema } = useCustomFieldSchema('contact');

  const setField = (key: keyof FormState, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    // Clear field error on change
    setFieldErrors((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  // ---------- Parse 422 errors ----------------------------------------------

  const handle422 = (err: AxiosError) => {
    const detail = (err.response?.data as { detail?: ApiError422Detail[] | string })?.detail;
    if (Array.isArray(detail)) {
      const errs: Record<string, string> = {};
      for (const item of detail) {
        if (item.field) errs[item.field] = item.error;
      }
      setFieldErrors(errs);
      toast.error('Please fix the highlighted errors');
    } else {
      toast.error(typeof detail === 'string' ? detail : 'Save failed');
    }
  };

  // ---------- Mutations -----------------------------------------------------

  const createMutation = useMutation<ContactDetail, AxiosError, ContactCreate>({
    mutationFn: (body) => contactsApi.create(body),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['contacts'] });
      toast.success('Contact created');
      navigate(`/contacts/${data.id}`);
    },
    onError: (err) => {
      if (err.response?.status === 422) {
        handle422(err);
      } else {
        toast.error('Failed to create contact');
      }
    },
  });

  const updateMutation = useMutation<ContactDetail, AxiosError, ContactUpdate>({
    mutationFn: (body) => contactsApi.update(id!, body),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['contacts'] });
      queryClient.invalidateQueries({ queryKey: ['contact', id] });
      toast.success('Contact updated');
      navigate(`/contacts/${data.id}`);
    },
    onError: (err) => {
      if (err.response?.status === 422) {
        handle422(err);
      } else {
        toast.error('Failed to update contact');
      }
    },
  });

  const isPending = createMutation.isPending || updateMutation.isPending;

  // ---------- Submit --------------------------------------------------------

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFieldErrors({});

    const body: ContactCreate = {
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      nickname: form.nickname.trim() || null,
      suffix: form.suffix.trim() || null,
      contact_type: form.contact_type || undefined,
      contact_subtype: form.contact_subtype.trim() || null,
      gender: form.gender || null,
      birth_date: form.birth_date || null,
      phone: form.phone.trim() || null,
      email: form.email.trim() || null,
      street_address: form.street_address.trim() || null,
      custom_data: Object.keys(customData).length > 0 ? (customData as Record<string, unknown>) : undefined,
    };

    if (mode === 'create') {
      createMutation.mutate(body);
    } else {
      updateMutation.mutate(body);
    }
  };

  // ---------- Render --------------------------------------------------------

  if (mode === 'edit' && contactLoading) {
    return (
      <div className="flex h-screen flex-col">
        <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => navigate(-1)}
              aria-label="Cancel"
              className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <ArrowLeft size={18} aria-hidden="true" />
            </button>
            <h1 className="text-lg font-bold text-foreground">Edit Contact</h1>
          </div>
        </header>
        <main className="flex-1 overflow-y-auto p-4">
          <LoadingState message="Loading contact…" />
        </main>
      </div>
    );
  }

  const title = mode === 'create' ? 'New Contact' : 'Edit Contact';

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => navigate(-1)}
            aria-label="Cancel"
            className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <ArrowLeft size={18} aria-hidden="true" />
          </button>
          <h1 className="text-lg font-bold text-foreground">{title}</h1>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-4 pb-24">
        <form onSubmit={handleSubmit} noValidate className="space-y-4 max-w-lg mx-auto">
          {/* Name fields */}
          <div className="bg-card rounded-2xl border border-border p-4 space-y-4">
            <h2 className="text-sm font-semibold text-foreground">Name</h2>

            <FormField
              label="First name"
              htmlFor="first_name"
              required
              error={fieldErrors.first_name}
            >
              <input
                id="first_name"
                type="text"
                value={form.first_name}
                onChange={(e) => setField('first_name', e.target.value)}
                placeholder="First name"
                className={inputClass}
                autoComplete="given-name"
              />
            </FormField>

            <FormField
              label="Last name"
              htmlFor="last_name"
              required
              error={fieldErrors.last_name}
            >
              <input
                id="last_name"
                type="text"
                value={form.last_name}
                onChange={(e) => setField('last_name', e.target.value)}
                placeholder="Last name"
                className={inputClass}
                autoComplete="family-name"
              />
            </FormField>

            <FormField
              label="Nickname"
              htmlFor="nickname"
              error={fieldErrors.nickname}
            >
              <input
                id="nickname"
                type="text"
                value={form.nickname}
                onChange={(e) => setField('nickname', e.target.value)}
                placeholder="Nickname"
                className={inputClass}
              />
            </FormField>

            <FormField
              label="Suffix"
              htmlFor="suffix"
              error={fieldErrors.suffix}
            >
              <input
                id="suffix"
                type="text"
                value={form.suffix}
                onChange={(e) => setField('suffix', e.target.value)}
                placeholder="e.g. Jr., Sr., III"
                className={inputClass}
              />
            </FormField>
          </div>

          {/* Classification */}
          <div className="bg-card rounded-2xl border border-border p-4 space-y-4">
            <h2 className="text-sm font-semibold text-foreground">Classification</h2>

            <FormField
              label="Contact type"
              htmlFor="contact_type"
              error={fieldErrors.contact_type}
            >
              <select
                id="contact_type"
                value={form.contact_type}
                onChange={(e) => setField('contact_type', e.target.value)}
                className={inputClass}
              >
                <option value="individual">Individual</option>
                <option value="organization">Organization</option>
                <option value="household">Household</option>
              </select>
            </FormField>

            <FormField
              label="Contact subtype"
              htmlFor="contact_subtype"
              error={fieldErrors.contact_subtype}
            >
              <input
                id="contact_subtype"
                type="text"
                value={form.contact_subtype}
                onChange={(e) => setField('contact_subtype', e.target.value)}
                placeholder="Subtype"
                className={inputClass}
              />
            </FormField>

            <FormField
              label="Gender"
              htmlFor="gender"
              error={fieldErrors.gender}
            >
              <select
                id="gender"
                value={form.gender}
                onChange={(e) => setField('gender', e.target.value)}
                className={inputClass}
              >
                <option value="">Select gender</option>
                <option value="male">Male</option>
                <option value="female">Female</option>
                <option value="other">Other</option>
                <option value="prefer_not_to_say">Prefer not to say</option>
              </select>
            </FormField>

            <FormField
              label="Birth date"
              htmlFor="birth_date"
              error={fieldErrors.birth_date}
            >
              <input
                id="birth_date"
                type="date"
                value={form.birth_date}
                onChange={(e) => setField('birth_date', e.target.value)}
                className={inputClass}
              />
            </FormField>
          </div>

          {/* Contact info */}
          <div className="bg-card rounded-2xl border border-border p-4 space-y-4">
            <h2 className="text-sm font-semibold text-foreground">Contact Info</h2>

            <FormField
              label="Phone"
              htmlFor="phone"
              error={fieldErrors.phone}
            >
              <input
                id="phone"
                type="tel"
                value={form.phone}
                onChange={(e) => setField('phone', e.target.value)}
                placeholder="Phone number"
                className={inputClass}
                autoComplete="tel"
              />
            </FormField>

            <FormField
              label="Email"
              htmlFor="email"
              error={fieldErrors.email}
            >
              <input
                id="email"
                type="email"
                value={form.email}
                onChange={(e) => setField('email', e.target.value)}
                placeholder="Email address"
                className={inputClass}
                autoComplete="email"
              />
            </FormField>

            <FormField
              label="Street address"
              htmlFor="street_address"
              error={fieldErrors.street_address}
            >
              <input
                id="street_address"
                type="text"
                value={form.street_address}
                onChange={(e) => setField('street_address', e.target.value)}
                placeholder="Street address"
                className={inputClass}
                autoComplete="street-address"
              />
            </FormField>
          </div>

          {/* Custom fields */}
          {schema?.groups && schema.groups.length > 0 && (
            <div className="bg-card rounded-2xl border border-border p-4 space-y-4">
              <h2 className="text-sm font-semibold text-foreground">Custom Fields</h2>
              {schema.groups
                .filter((g) => g.is_active)
                .map((group) => (
                  <CustomFieldsSection
                    key={group.id}
                    group={group}
                    value={customData}
                    onChange={(name, v) =>
                      setCustomData((prev) => ({ ...prev, [name]: v }))
                    }
                    errors={fieldErrors}
                  />
                ))}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={() => navigate(-1)}
              className="flex h-11 flex-1 items-center justify-center rounded-xl border border-border bg-background text-sm font-semibold text-foreground transition-colors hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isPending}
              className="flex h-11 flex-1 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {isPending ? 'Saving…' : mode === 'create' ? 'Create Contact' : 'Save Changes'}
            </button>
          </div>
        </form>
      </main>
    </div>
  );
}
