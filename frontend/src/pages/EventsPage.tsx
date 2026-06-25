import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  Calendar,
  CalendarDays,
  MapPin,
  Plus,
  Radio,
  Search,
  Users,
} from 'lucide-react';
import { api } from '@/services/api';
import { listEvents } from '@/services/events';
import { useAuthStore } from '@/store/authStore';
import { LoadingState } from '@/components/ui/StateViews';
import { Pagination } from '@/components/ui/Pagination';
import { EventTypeBadge } from '@/components/events/EventTypeBadge';
import { SessionTimeBadge } from '@/components/events/SessionTimeBadge';
import { EventFormDrawer } from '@/components/events/EventFormDrawer';
import type { Event } from '@/types';

// ---------- Constants ---------------------------------------------------------

const EVENT_TYPES = [
  'Sunday Celebration',
  'Prayer Meeting',
  'Powerhouse',
  'Community Meeting',
  'Conference',
  'Event',
] as const;

const EVENT_TYPE_LABELS: Record<string, string> = {
  'Sunday Celebration': 'Sunday Celebration',
  'Prayer Meeting': 'Prayer Meeting',
  'Powerhouse': 'Powerhouse',
  'Community Meeting': 'Community Meeting',
  'Conference': 'Conference',
  'Event': 'Event',
};

const PAGE_SIZE = 20;

// ---------- Helpers -----------------------------------------------------------

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timerRef.current);
  }, [value, delay]);

  return debounced;
}

async function fetchActiveEventId(): Promise<number | null> {
  const res = await api.get('/events/active-event-id');
  return res.data.active_event_id ?? null;
}

function formatOccurrenceDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '—';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr;
  return d.toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

// ---------- EventCard ---------------------------------------------------------

interface EventCardProps {
  event: Event & { participant_counts?: { unique_count: number; total_count: number } };
  isActive: boolean;
  isAdmin: boolean;
  settingActive: number | null;
  onSetActive: (id: number) => void;
}

function EventCard({
  event,
  isActive,
  isAdmin,
  settingActive,
  onSetActive,
}: EventCardProps) {
  return (
    <div
      className={`rounded-2xl border bg-card p-4 shadow-sm transition-all ${
        isActive ? 'border-primary ring-2 ring-primary/20' : 'border-border'
      }`}
    >
      <div className="flex items-start gap-3">
        {/* Icon */}
        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${
            isActive ? 'bg-primary/30' : 'bg-primary/20'
          }`}
        >
          <Calendar size={18} className="text-primary" aria-hidden="true" />
        </div>

        {/* Main content */}
        <div className="min-w-0 flex-1">
          {/* Title row */}
          <div className="flex flex-wrap items-center gap-1.5">
            <h3 className="text-sm font-bold text-foreground">{event.title}</h3>
            {isActive && (
              <span className="flex items-center gap-1 rounded-full bg-primary/20 px-2 py-0.5 text-[10px] font-bold text-primary">
                <Radio size={10} aria-hidden="true" />
                LIVE
              </span>
            )}
          </div>

          {/* Badges row */}
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {event.event_type && (
              <EventTypeBadge eventType={event.event_type} />
            )}
            {event.session_time && (
              <SessionTimeBadge sessionTime={event.session_time} />
            )}
          </div>

          {/* Date + location */}
          <p className="mt-1.5 text-xs text-foreground/50">
            {formatOccurrenceDate(event.occurrence_date)}
          </p>
          {event.location && (
            <p className="mt-0.5 flex items-center gap-1 text-xs text-foreground/50">
              <MapPin size={11} aria-hidden="true" />
              {event.location}
            </p>
          )}

          {/* Attendee chip */}
          {event.participant_counts !== undefined && (
            <div className="mt-1.5">
              <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs text-foreground/60">
                <Users size={11} aria-hidden="true" />
                {event.participant_counts.unique_count} attendee
                {event.participant_counts.unique_count !== 1 ? 's' : ''}
              </span>
            </div>
          )}
        </div>

        {/* Admin: Set Active button (only when card is not already active) */}
        {isAdmin && !isActive && (
          <button
            type="button"
            onClick={() => onSetActive(event.id)}
            disabled={settingActive === event.id}
            aria-label={`Set ${event.title} as active event`}
            className="shrink-0 rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground transition-all hover:bg-primary/10 disabled:opacity-50"
          >
            {settingActive === event.id ? 'Setting…' : 'Set Active'}
          </button>
        )}
      </div>
    </div>
  );
}

// ---------- Main Page ---------------------------------------------------------

export function EventsPage() {
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const queryClient = useQueryClient();

  // Filter state
  const [typeFilter, setTypeFilter] = useState<string | undefined>();
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const search = useDebounce(searchInput, 300);

  // Pagination
  const [page, setPage] = useState(1);
  const pageSize = PAGE_SIZE;

  // Drawer
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Active event management
  const [settingActive, setSettingActive] = useState<number | null>(null);

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [typeFilter, dateFrom, dateTo, search]);

  // Build query params — matches TanStack key spec
  const queryParams = useMemo(
    () => ({
      type: typeFilter,
      dateFrom: dateFrom || undefined,
      dateTo: dateTo || undefined,
      seriesId: undefined as number | undefined,
      isActive: undefined as boolean | undefined,
      page,
      pageSize,
    }),
    [typeFilter, dateFrom, dateTo, page, pageSize],
  );

  // listEvents params use snake_case API fields
  const apiParams = useMemo(
    () => ({
      type: queryParams.type,
      date_from: queryParams.dateFrom,
      date_to: queryParams.dateTo,
      series_id: queryParams.seriesId,
      is_active: queryParams.isActive,
      page: queryParams.page,
      page_size: queryParams.pageSize,
      // title search — pass as 'search' if backend supports it; spec asks for client-side filtering
    }),
    [queryParams],
  );

  const { data, isLoading } = useQuery({
    queryKey: ['events', queryParams],
    queryFn: () => listEvents(apiParams),
  });

  const { data: activeEventId, refetch: refetchActive } = useQuery({
    queryKey: ['active-event-id'],
    queryFn: fetchActiveEventId,
    enabled: isAdmin,
  });

  // Client-side title search filter applied on top of server-side results
  const allItems = data?.items ?? [];
  const items = search
    ? allItems.filter((e) =>
        e.title.toLowerCase().includes(search.toLowerCase()),
      )
    : allItems;

  const total = search ? items.length : (data?.total ?? 0);

  // ---------- Handlers --------------------------------------------------------

  const handleSetActive = async (eventId: number) => {
    setSettingActive(eventId);
    try {
      const res = await api.post(`/events/set-active?event_id=${eventId}`);
      toast.success(res.data.message);
      refetchActive();
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Failed to set active event');
    } finally {
      setSettingActive(null);
    }
  };

  const handleClearActive = async () => {
    setSettingActive(-1);
    try {
      const res = await api.post('/events/set-active');
      toast.success(res.data.message);
      refetchActive();
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axiosErr.response?.data?.detail ?? 'Failed to clear active event');
    } finally {
      setSettingActive(null);
    }
  };

  const handleDrawerSaved = () => {
    queryClient.invalidateQueries({ queryKey: ['events'] });
    setDrawerOpen(false);
  };

  const toggleTypeFilter = (type: string) => {
    setTypeFilter((prev) => (prev === type ? undefined : type));
  };

  // Title of active event for banner (sourced from paginated items)
  const activeEventTitle = activeEventId
    ? (items.find((e: Event) => e.id === activeEventId)?.title ?? `Event #${activeEventId}`)
    : null;

  // ---------- Render ----------------------------------------------------------

  return (
    <div className="flex h-screen flex-col">
      {/* Header */}
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-bold text-foreground">Events</h1>
          {isAdmin && (
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              aria-label="New Event"
              className="flex min-h-[36px] items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98] focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <Plus size={15} aria-hidden="true" />
              New Event
            </button>
          )}
        </div>
      </header>

      {/* Active-event banner */}
      {isAdmin && (
        <div className="border-b border-border bg-card px-4 py-2">
          <p className="text-xs text-foreground/50">
            <span className="font-semibold">Active event for camera detections: </span>
            {activeEventTitle ? (
              <>
                {activeEventTitle}
                <button
                  type="button"
                  onClick={handleClearActive}
                  disabled={settingActive === -1}
                  aria-label="Clear active event"
                  className="ml-2 text-[10px] text-foreground/40 underline hover:text-red-500 disabled:opacity-50"
                >
                  clear
                </button>
              </>
            ) : (
              <span className="font-semibold text-amber-600">
                None set — detections won't be attributed to an event
              </span>
            )}
          </p>
        </div>
      )}

      {/* Filter bar */}
      <div className="border-b border-border bg-background px-3 py-3 space-y-2">
        {/* Title search */}
        <div className="relative">
          <Search
            size={16}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-foreground/40"
            aria-hidden="true"
          />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search events…"
            aria-label="Search events"
            className="h-10 w-full rounded-xl border border-border bg-card pl-9 pr-3 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
          />
        </div>

        {/* Date range */}
        <div className="flex gap-2">
          <label className="flex-1">
            <span className="sr-only">From date</span>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              aria-label="From date"
              className="h-9 w-full rounded-xl border border-border bg-card px-3 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
            />
          </label>
          <label className="flex-1">
            <span className="sr-only">To date</span>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              aria-label="To date"
              className="h-9 w-full rounded-xl border border-border bg-card px-3 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
            />
          </label>
          {(dateFrom || dateTo) && (
            <button
              type="button"
              onClick={() => { setDateFrom(''); setDateTo(''); }}
              aria-label="Clear date range"
              className="rounded-xl border border-border bg-card px-3 text-xs text-foreground/60 hover:bg-muted"
            >
              Clear
            </button>
          )}
        </div>

        {/* EventType pill filters */}
        <div className="flex flex-wrap gap-2" role="group" aria-label="Event type filters">
          {/* All pill */}
          <button
            type="button"
            aria-pressed={typeFilter === undefined}
            onClick={() => setTypeFilter(undefined)}
            className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
              typeFilter === undefined
                ? 'border-primary bg-primary text-primary-foreground'
                : 'border-border bg-card text-foreground/60 hover:bg-primary/10 hover:text-primary'
            }`}
          >
            All
          </button>

          {EVENT_TYPES.map((et) => (
            <button
              key={et}
              type="button"
              aria-pressed={typeFilter === et}
              onClick={() => toggleTypeFilter(et)}
              className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
                typeFilter === et
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'border-border bg-card text-foreground/60 hover:bg-primary/10 hover:text-primary'
              }`}
            >
              {EVENT_TYPE_LABELS[et]}
            </button>
          ))}
        </div>
      </div>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {isLoading ? (
          <LoadingState message="Loading events…" />
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-foreground/50">
            <CalendarDays size={40} className="mb-3 opacity-40" aria-hidden="true" />
            <p className="text-sm font-medium">No events yet</p>
            {isAdmin && (
              <button
                type="button"
                onClick={() => setDrawerOpen(true)}
                className="mt-3 flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground shadow-sm hover:bg-primary/85 focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <Plus size={13} aria-hidden="true" />
                Create Event
              </button>
            )}
          </div>
        ) : (
          <div className="space-y-2 pb-4">
            {items.map((event) => {
              const isActive = isAdmin && event.id === activeEventId;
              return (
                <EventCard
                  key={event.id}
                  event={event as Event & { participant_counts?: { unique_count: number; total_count: number } }}
                  isActive={isActive}
                  isAdmin={isAdmin}
                  settingActive={settingActive}
                  onSetActive={handleSetActive}
                />
              );
            })}
          </div>
        )}
      </main>

      {/* Pagination */}
      {!isLoading && total > 0 && (
        <div className="border-t border-border bg-card px-3 py-1 pb-safe">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onPageChange={setPage}
          />
        </div>
      )}

      {/* New / Edit Event drawer */}
      <EventFormDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onSaved={handleDrawerSaved}
      />
    </div>
  );
}
