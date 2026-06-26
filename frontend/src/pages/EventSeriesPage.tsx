/**
 * EventSeriesPage — /settings/event-series
 * AdminRoute-protected page for managing event series.
 *
 * Features:
 * - Series cards with title, event_type badge, cadence description, is_active toggle
 * - Create/edit via inline form sections
 * - Per-series Generate button with date picker calling generateSeriesOccurrences
 */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft, Calendar, ChevronDown, ChevronUp, Plus } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { AxiosError } from 'axios';

import {
  listEventSeries,
  createEventSeries,
  updateEventSeries,
  generateSeriesOccurrences,
  type EventSeriesCreate,
  type EventSeriesUpdate,
} from '@/services/events';
import { EventTypeBadge } from '@/components/events/EventTypeBadge';
import { FormField, inputClass } from '@/components/ui/FormField';
import { LoadingState } from '@/components/ui/StateViews';
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

// ---------- Cadence description -----------------------------------------------

function cadenceDescription(series: EventSeries): string {
  const parts: string[] = [];
  if (series.default_session_time) {
    const label =
      series.default_session_time.charAt(0).toUpperCase() +
      series.default_session_time.slice(1);
    parts.push(label);
  }
  if (series.default_location) {
    parts.push(`at ${series.default_location}`);
  }
  return parts.length > 0 ? parts.join(' ') : 'No cadence configured';
}

// ---------- SeriesForm --------------------------------------------------------

interface SeriesFormState {
  name: string;
  event_type: string;
  default_session_time: string;
  default_location: string;
  is_active: boolean;
}

function emptySeriesForm(): SeriesFormState {
  return {
    name: '',
    event_type: '',
    default_session_time: '',
    default_location: '',
    is_active: true,
  };
}

function seedSeriesForm(series: EventSeries): SeriesFormState {
  return {
    name: series.name,
    event_type: series.event_type ?? '',
    default_session_time: series.default_session_time ?? '',
    default_location: series.default_location ?? '',
    is_active: series.is_active,
  };
}

interface SeriesFormProps {
  initial: SeriesFormState;
  isPending: boolean;
  onSubmit: (form: SeriesFormState) => void;
  onCancel: () => void;
  submitLabel: string;
}

function SeriesForm({
  initial,
  isPending,
  onSubmit,
  onCancel,
  submitLabel,
}: SeriesFormProps) {
  const [form, setForm] = useState<SeriesFormState>(initial);

  const setField = <K extends keyof SeriesFormState>(
    key: K,
    value: SeriesFormState[K],
  ) => setForm((prev) => ({ ...prev, [key]: value }));

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(form);
      }}
      noValidate
      className="space-y-3 p-4 bg-background rounded-xl border border-border mt-3"
    >
      <FormField label="Name" htmlFor="sf-name" required>
        <input
          id="sf-name"
          type="text"
          value={form.name}
          onChange={(e) => setField('name', e.target.value)}
          placeholder="Series name"
          required
          className={inputClass}
        />
      </FormField>

      <FormField label="Event Type" htmlFor="sf-event_type" required>
        <select
          id="sf-event_type"
          value={form.event_type}
          onChange={(e) => setField('event_type', e.target.value)}
          required
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

      <FormField label="Default Session Time" htmlFor="sf-session_time">
        <select
          id="sf-session_time"
          value={form.default_session_time}
          onChange={(e) => setField('default_session_time', e.target.value)}
          className={inputClass}
        >
          <option value="">None</option>
          {SESSION_TIME_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </FormField>

      <FormField label="Default Location" htmlFor="sf-location">
        <input
          id="sf-location"
          type="text"
          value={form.default_location}
          onChange={(e) => setField('default_location', e.target.value)}
          placeholder="e.g. Main Sanctuary"
          className={inputClass}
        />
      </FormField>

      <div className="flex items-center gap-2">
        <input
          id="sf-is_active"
          type="checkbox"
          checked={form.is_active}
          onChange={(e) => setField('is_active', e.target.checked)}
          className="h-4 w-4 rounded border-border accent-primary"
        />
        <label
          htmlFor="sf-is_active"
          className="text-sm font-medium text-foreground"
        >
          Active
        </label>
      </div>

      <div className="flex gap-2 pt-1">
        <button
          type="button"
          onClick={onCancel}
          className="flex h-9 flex-1 items-center justify-center rounded-xl border border-border bg-card text-xs font-semibold text-foreground hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={isPending}
          className="flex h-9 flex-1 items-center justify-center rounded-xl bg-primary text-xs font-bold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
        >
          {isPending ? 'Saving…' : submitLabel}
        </button>
      </div>
    </form>
  );
}

// ---------- GeneratePanel -----------------------------------------------------

interface GeneratePanelProps {
  series: EventSeries;
}

function GeneratePanel({ series }: GeneratePanelProps) {
  const [targetDate, setTargetDate] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);

  const handleGenerate = async () => {
    if (!targetDate) {
      toast.error('Please select a target date');
      return;
    }
    setIsGenerating(true);
    try {
      const result = await generateSeriesOccurrences(series.id, {
        target_date: targetDate,
      });
      toast.success(`Created ${result.created}, skipped ${result.skipped}`);
      setTargetDate('');
    } catch (err: unknown) {
      const axiosErr = err as AxiosError<{ detail?: string }>;
      toast.error(
        axiosErr.response?.data?.detail ?? 'Failed to generate occurrences',
      );
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <div className="mt-3 flex items-center gap-2 border-t border-border pt-3">
      <Calendar size={14} className="shrink-0 text-foreground/50" aria-hidden="true" />
      <input
        type="date"
        value={targetDate}
        onChange={(e) => setTargetDate(e.target.value)}
        aria-label={`Target date for generating ${series.name} occurrences`}
        className="h-8 flex-1 rounded-lg border border-border bg-background px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
      />
      <button
        type="button"
        onClick={handleGenerate}
        disabled={isGenerating || !targetDate}
        aria-label={`Generate occurrences for ${series.name}`}
        className="flex h-8 shrink-0 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-semibold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
      >
        {isGenerating ? 'Generating…' : 'Generate'}
      </button>
    </div>
  );
}

// ---------- SeriesCard --------------------------------------------------------

interface SeriesCardProps {
  series: EventSeries;
}

function SeriesCard({ series }: SeriesCardProps) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);

  const toggleMutation = useMutation<
    EventSeries,
    AxiosError<{ detail?: string }>,
    boolean
  >({
    mutationFn: (is_active) => updateEventSeries(series.id, { is_active }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['event-series'] });
    },
    onError: (err) => {
      toast.error(err.response?.data?.detail ?? 'Failed to update series');
    },
  });

  const updateMutation = useMutation<
    EventSeries,
    AxiosError<{ detail?: string }>,
    EventSeriesUpdate
  >({
    mutationFn: (payload) => updateEventSeries(series.id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['event-series'] });
      toast.success('Series updated');
      setExpanded(false);
    },
    onError: (err) => {
      toast.error(err.response?.data?.detail ?? 'Failed to update series');
    },
  });

  const handleToggleActive = () => {
    toggleMutation.mutate(!series.is_active);
  };

  const handleEditSubmit = (form: SeriesFormState) => {
    updateMutation.mutate({
      name: form.name.trim(),
      event_type: form.event_type || undefined,
      default_session_time: form.default_session_time || null,
      default_location: form.default_location.trim() || null,
      is_active: form.is_active,
    });
  };

  return (
    <div
      className={`rounded-2xl border bg-card p-4 shadow-sm transition-all ${
        series.is_active ? 'border-border' : 'border-border opacity-60'
      }`}
    >
      {/* Card header row */}
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-bold text-foreground">{series.name}</h3>
            {series.event_type && (
              <EventTypeBadge eventType={series.event_type} />
            )}
          </div>
          <p className="mt-0.5 text-xs text-foreground/50">
            {cadenceDescription(series)}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {/* is_active toggle */}
          <button
            type="button"
            role="switch"
            aria-checked={series.is_active}
            aria-label={`Toggle ${series.name} active`}
            disabled={toggleMutation.isPending}
            onClick={handleToggleActive}
            className={`relative inline-flex h-5 w-9 cursor-pointer items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50 ${
              series.is_active ? 'bg-primary' : 'bg-border'
            }`}
          >
            <span
              className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${
                series.is_active ? 'translate-x-4' : 'translate-x-0.5'
              }`}
            />
          </button>

          {/* Edit expand toggle */}
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-label={expanded ? 'Collapse edit form' : 'Expand edit form'}
            aria-expanded={expanded}
            className="flex h-8 w-8 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {expanded ? (
              <ChevronUp size={14} aria-hidden="true" />
            ) : (
              <ChevronDown size={14} aria-hidden="true" />
            )}
          </button>
        </div>
      </div>

      {/* Generate panel (always visible) */}
      <GeneratePanel series={series} />

      {/* Inline edit form */}
      {expanded && (
        <SeriesForm
          initial={seedSeriesForm(series)}
          isPending={updateMutation.isPending}
          onSubmit={handleEditSubmit}
          onCancel={() => setExpanded(false)}
          submitLabel="Save Changes"
        />
      )}
    </div>
  );
}

// ---------- Main Page ---------------------------------------------------------

export function EventSeriesPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);

  const { data: seriesList, isLoading } = useQuery<EventSeries[]>({
    queryKey: ['event-series'],
    queryFn: () => listEventSeries(),
  });

  const createMutation = useMutation<
    EventSeries,
    AxiosError<{ detail?: string }>,
    EventSeriesCreate
  >({
    mutationFn: (payload) => createEventSeries(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['event-series'] });
      toast.success('Series created');
      setCreateOpen(false);
    },
    onError: (err) => {
      toast.error(err.response?.data?.detail ?? 'Failed to create series');
    },
  });

  const handleCreateSubmit = (form: SeriesFormState) => {
    createMutation.mutate({
      name: form.name.trim(),
      event_type: form.event_type,
      default_session_time: form.default_session_time || null,
      default_location: form.default_location.trim() || null,
      is_active: form.is_active,
    });
  };

  return (
    <div className="flex h-screen flex-col">
      {/* Header */}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => navigate('/settings')}
            aria-label="Back to Settings"
            className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <ArrowLeft size={18} aria-hidden="true" />
          </button>
          <h1 className="text-lg font-bold text-foreground">Event Series</h1>

          <div className="ml-auto">
            <button
              type="button"
              onClick={() => setCreateOpen((v) => !v)}
              aria-label="New Series"
              aria-expanded={createOpen}
              className="flex min-h-[36px] items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98] focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <Plus size={15} aria-hidden="true" />
              New Series
            </button>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto px-4 pt-4 pb-24">
        {/* Create form */}
        {createOpen && (
          <div className="mb-4 rounded-2xl border border-primary/40 bg-card p-4 shadow-sm">
            <h2 className="mb-1 text-sm font-semibold text-foreground">
              New Series
            </h2>
            <SeriesForm
              initial={emptySeriesForm()}
              isPending={createMutation.isPending}
              onSubmit={handleCreateSubmit}
              onCancel={() => setCreateOpen(false)}
              submitLabel="Create Series"
            />
          </div>
        )}

        {/* Series list */}
        {isLoading ? (
          <LoadingState message="Loading series…" />
        ) : !seriesList || seriesList.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-foreground/50">
            <Calendar size={40} className="mb-3 opacity-40" aria-hidden="true" />
            <p className="text-sm font-medium">No event series yet</p>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="mt-3 flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground shadow-sm hover:bg-primary/85 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <Plus size={13} aria-hidden="true" />
              Create Series
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            {seriesList.map((series) => (
              <SeriesCard key={series.id} series={series} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
