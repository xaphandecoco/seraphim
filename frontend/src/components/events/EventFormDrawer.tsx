/**
 * EventFormDrawer — slide-in drawer for creating / editing events.
 * Mobile: bottom sheet. md+: right-side panel.
 * Controlled form with POST /events or PATCH /events/{id}.
 * 422 errors surfaced via toast.error(err.response?.data?.detail).
 */

import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { X } from 'lucide-react';
import type { AxiosError } from 'axios';

import { FormField, inputClass } from '@/components/ui/FormField';
import {
  createEvent,
  updateEvent,
  getEvent,
  listEventSeries,
  type EventCreate,
  type EventUpdate,
} from '@/services/events';
import type { EventSeries } from '@/types';

// ---------- Constants ---------------------------------------------------------

const EVENT_TYPE_OPTIONS = [
  { value: 'Sunday Celebration', label: 'Sunday Celebration' },
  { value: 'Prayer Meeting', label: 'Prayer Meeting' },
  { value: 'Powerhouse', label: 'Powerhouse' },
  { value: 'Community Meeting', label: 'Community Meeting' },
  { value: 'Conference', label: 'Conference' },
  { value: 'Event', label: 'Event' },
] as const;

const SESSION_TIME_OPTIONS = [
  { value: '8AM', label: '8AM' },
  { value: '10AM', label: '10AM' },
  { value: '3PM', label: '3PM' },
] as const;

// ---------- Form state --------------------------------------------------------

interface FormState {
  title: string;
  event_type: string;
  session_time: string;
  occurrence_date: string;
  start_at: string;
  end_at: string;
  location: string;
  recurring_series_id: string;
}

function emptyForm(): FormState {
  return {
    title: '',
    event_type: '',
    session_time: '',
    occurrence_date: '',
    start_at: '',
    end_at: '',
    location: '',
    recurring_series_id: '',
  };
}

// ---------- Props -------------------------------------------------------------

export interface EventFormDrawerProps {
  open: boolean;
  onClose: () => void;
  /** When provided the drawer operates in edit mode; omit for create. */
  eventId?: number;
  /** Called after a successful save so the parent can invalidate queries. */
  onSaved?: () => void;
}

// ---------- Component ---------------------------------------------------------

export function EventFormDrawer({
  open,
  onClose,
  eventId,
  onSaved,
}: EventFormDrawerProps) {
  const isEditMode = !!eventId;
  const queryClient = useQueryClient();

  const [form, setForm] = useState<FormState>(emptyForm());

  // Fetch existing event when editing
  const { data: existingEvent, isLoading: eventLoading } = useQuery({
    queryKey: ['event', eventId],
    queryFn: () => getEvent(eventId!),
    enabled: isEditMode && open,
  });

  // Fetch series list for recurring_series_id select
  const { data: seriesList } = useQuery<EventSeries[]>({
    queryKey: ['event-series'],
    queryFn: () => listEventSeries(),
    enabled: open,
  });

  // Seed form when existing event loads
  useEffect(() => {
    if (isEditMode && existingEvent) {
      // Convert ISO datetimes to datetime-local format (slice to 16 chars)
      const toLocal = (iso: string | null | undefined) =>
        iso ? iso.slice(0, 16) : '';

      setForm({
        title: existingEvent.title ?? '',
        event_type: existingEvent.event_type ?? '',
        session_time: existingEvent.session_time ?? '',
        occurrence_date: existingEvent.occurrence_date ?? '',
        start_at: toLocal(existingEvent.start_at),
        end_at: toLocal(existingEvent.end_at),
        location: existingEvent.location ?? '',
        recurring_series_id: existingEvent.recurring_series_id
          ? String(existingEvent.recurring_series_id)
          : '',
      });
    } else if (!isEditMode && open) {
      setForm(emptyForm());
    }
  }, [isEditMode, existingEvent, open]);

  // Reset form on close
  useEffect(() => {
    if (!open) {
      setForm(emptyForm());
    }
  }, [open]);

  const setField = <K extends keyof FormState>(key: K, value: string) => {
    setForm((prev) => {
      const next = { ...prev, [key]: value };
      // Auto-clear session_time when event_type changes away from Sunday Celebration
      if (key === 'event_type' && value !== 'Sunday Celebration') {
        next.session_time = '';
      }
      return next;
    });
  };

  // session_time is only enabled when event_type is 'Sunday Celebration'
  const sessionTimeEnabled = form.event_type === 'Sunday Celebration';

  // ---------- Mutations -------------------------------------------------------

  const createMutation = useMutation({
    mutationFn: (payload: EventCreate) => createEvent(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['events'] });
      toast.success('Event created');
      onSaved?.();
      onClose();
    },
    onError: (err: AxiosError<{ detail?: string }>) => {
      const detail = err.response?.data?.detail;
      toast.error(detail ?? 'Failed to create event');
    },
  });

  const updateMutation = useMutation({
    mutationFn: (payload: EventUpdate) => updateEvent(eventId!, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['events'] });
      queryClient.invalidateQueries({ queryKey: ['event', eventId] });
      toast.success('Event updated');
      onSaved?.();
      onClose();
    },
    onError: (err: AxiosError<{ detail?: string }>) => {
      const detail = err.response?.data?.detail;
      toast.error(detail ?? 'Failed to update event');
    },
  });

  const isPending = createMutation.isPending || updateMutation.isPending;

  // ---------- Submit ----------------------------------------------------------

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    const payload: EventCreate = {
      title: form.title.trim(),
      event_type: form.event_type || null,
      session_time: sessionTimeEnabled ? (form.session_time || null) : null,
      occurrence_date: form.occurrence_date || null,
      start_at: form.start_at || null,
      end_at: form.end_at || null,
      location: form.location.trim() || null,
      recurring_series_id: form.recurring_series_id
        ? Number(form.recurring_series_id)
        : null,
    };

    if (isEditMode) {
      updateMutation.mutate(payload as EventUpdate);
    } else {
      createMutation.mutate(payload);
    }
  };

  if (!open) return null;

  const title = isEditMode ? 'Edit Event' : 'New Event';

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/*
        Mobile: bottom sheet (fixed bottom, rounded top corners).
        md+: right-side panel (fixed right, full height, slide in from right).
      */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={[
          'fixed z-50 bg-card shadow-xl',
          // Mobile bottom sheet
          'bottom-0 left-0 right-0 max-h-[92dvh] rounded-t-2xl',
          // md+ right side drawer
          'md:bottom-auto md:left-auto md:right-0 md:top-0 md:h-full md:w-[420px] md:max-h-full md:rounded-none md:rounded-l-2xl',
          'flex flex-col overflow-hidden',
        ].join(' ')}
      >
        {/* Drawer header */}
        <div className="flex shrink-0 items-center justify-between border-b border-border px-5 py-4">
          <h2 className="text-base font-bold text-foreground">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-xl p-1.5 text-foreground/40 transition-colors hover:bg-muted hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto p-5">
          {isEditMode && eventLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-primary" />
            </div>
          ) : (
            <form
              id="event-form"
              onSubmit={handleSubmit}
              noValidate
              className="space-y-4"
            >
              <FormField label="Title" htmlFor="ef-title" required>
                <input
                  id="ef-title"
                  type="text"
                  value={form.title}
                  onChange={(e) => setField('title', e.target.value)}
                  placeholder="Event title"
                  required
                  className={inputClass}
                />
              </FormField>

              <FormField label="Event Type" htmlFor="ef-event_type">
                <select
                  id="ef-event_type"
                  value={form.event_type}
                  onChange={(e) => setField('event_type', e.target.value)}
                  className={inputClass}
                >
                  <option value="">Select type…</option>
                  {EVENT_TYPE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </FormField>

              <FormField
                label="Session Time"
                htmlFor="ef-session_time"
                hint={
                  sessionTimeEnabled
                    ? undefined
                    : 'Available for Sunday Celebration only'
                }
              >
                <select
                  id="ef-session_time"
                  value={form.session_time}
                  onChange={(e) => setField('session_time', e.target.value)}
                  disabled={!sessionTimeEnabled}
                  className={inputClass}
                >
                  <option value="">Select session…</option>
                  {SESSION_TIME_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </FormField>

              <FormField label="Occurrence Date" htmlFor="ef-occurrence_date">
                <input
                  id="ef-occurrence_date"
                  type="date"
                  value={form.occurrence_date}
                  onChange={(e) => setField('occurrence_date', e.target.value)}
                  className={inputClass}
                />
              </FormField>

              <FormField label="Start" htmlFor="ef-start_at">
                <input
                  id="ef-start_at"
                  type="datetime-local"
                  value={form.start_at}
                  onChange={(e) => setField('start_at', e.target.value)}
                  className={inputClass}
                />
              </FormField>

              <FormField label="End" htmlFor="ef-end_at">
                <input
                  id="ef-end_at"
                  type="datetime-local"
                  value={form.end_at}
                  onChange={(e) => setField('end_at', e.target.value)}
                  className={inputClass}
                />
              </FormField>

              <FormField label="Location" htmlFor="ef-location">
                <input
                  id="ef-location"
                  type="text"
                  value={form.location}
                  onChange={(e) => setField('location', e.target.value)}
                  placeholder="e.g. Main Sanctuary"
                  className={inputClass}
                />
              </FormField>

              <FormField label="Series" htmlFor="ef-recurring_series_id">
                <select
                  id="ef-recurring_series_id"
                  value={form.recurring_series_id}
                  onChange={(e) =>
                    setField('recurring_series_id', e.target.value)
                  }
                  className={inputClass}
                >
                  <option value="">No series</option>
                  {(seriesList ?? []).map((s) => (
                    <option key={s.id} value={String(s.id)}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </FormField>
            </form>
          )}
        </div>

        {/* Drawer footer */}
        <div className="shrink-0 border-t border-border px-5 py-4">
          <div className="flex gap-3">
            <button
              type="button"
              onClick={onClose}
              className="flex h-11 flex-1 items-center justify-center rounded-xl border border-border bg-background text-sm font-semibold text-foreground transition-colors hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
            >
              Cancel
            </button>
            <button
              type="submit"
              form="event-form"
              disabled={isPending || (isEditMode && eventLoading)}
              className="flex h-11 flex-1 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {isPending
                ? 'Saving…'
                : isEditMode
                  ? 'Save Changes'
                  : 'Create Event'}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
