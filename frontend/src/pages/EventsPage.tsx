import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Calendar, CalendarDays, RefreshCw, Radio } from 'lucide-react';
import { api } from '@/services/api';
import { useAuthStore } from '@/store/authStore';
import type { ChurchEvent } from '@/types';

async function fetchEvents(): Promise<ChurchEvent[]> {
  const res = await api.get('/events');
  return res.data;
}

async function fetchActiveEventId(): Promise<number | null> {
  const res = await api.get('/events/active-event-id');
  return res.data.active_event_id ?? null;
}

export function EventsPage() {
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const queryClient = useQueryClient();
  const [syncing, setSyncing] = useState(false);
  const [settingActive, setSettingActive] = useState<number | null>(null);

  const { data: events = [], isLoading } = useQuery({
    queryKey: ['events'],
    queryFn: fetchEvents,
  });

  const { data: activeEventId, refetch: refetchActive } = useQuery({
    queryKey: ['active-event-id'],
    queryFn: fetchActiveEventId,
    enabled: isAdmin,
  });

  const syncEvents = async () => {
    setSyncing(true);
    try {
      await api.post('/events/sync');
      await queryClient.invalidateQueries({ queryKey: ['events'] });
      toast.success('Events synced');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Sync failed');
    } finally {
      setSyncing(false);
    }
  };

  const setActiveEvent = async (eventId: number | null) => {
    setSettingActive(eventId ?? -1);
    try {
      const url = eventId ? `/events/set-active?event_id=${eventId}` : '/events/set-active';
      const res = await api.post(url);
      toast.success(res.data.message);
      refetchActive();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to set active event');
    } finally {
      setSettingActive(null);
    }
  };

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-bold text-foreground">Events</h1>
          {isAdmin && (
            <button
              onClick={syncEvents}
              disabled={syncing}
              aria-label="Sync events"
              className="flex items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground transition-all hover:bg-primary/10 disabled:opacity-50"
            >
              <RefreshCw size={13} className={syncing ? 'animate-spin' : ''} aria-hidden="true" />
              {syncing ? 'Syncing…' : 'Sync'}
            </button>
          )}
        </div>
      </header>

      {/* Active event banner */}
      {isAdmin && (
        <div className="border-b border-border bg-card px-4 py-2">
          <p className="text-xs text-foreground/50">
            <span className="font-semibold">Active event for camera detections: </span>
            {activeEventId
              ? events.find((e) => e.id === activeEventId)?.title ?? `Event #${activeEventId}`
              : <span className="text-amber-600 font-semibold">None set — detections won't be attributed to an event</span>
            }
            {activeEventId && (
              <button
                onClick={() => setActiveEvent(null)}
                className="ml-2 text-foreground/40 hover:text-red-500 underline text-[10px]"
                aria-label="Clear active event"
              >
                clear
              </button>
            )}
          </p>
        </div>
      )}

      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {isLoading ? (
          <div className="py-16 text-center text-sm text-foreground/50">Loading…</div>
        ) : events.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-foreground/50">
            <CalendarDays size={40} className="mb-3 opacity-40" aria-hidden="true" />
            <p className="text-sm font-medium">No upcoming events</p>
            {isAdmin && <p className="mt-1 text-xs">Use Sync to pull events</p>}
          </div>
        ) : (
          <div className="space-y-2 pb-4">
            {events.map((event) => {
              const isActive = isAdmin && event.id === activeEventId;
              return (
                <div
                  key={event.id}
                  className={`rounded-2xl border bg-card p-4 shadow-sm transition-all ${
                    isActive ? 'border-primary ring-2 ring-primary/20' : 'border-border'
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${isActive ? 'bg-primary/30' : 'bg-primary/20'}`}>
                      <Calendar size={18} className="text-primary" aria-hidden="true" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <h3 className="text-sm font-bold text-foreground">{event.title}</h3>
                        {isActive && (
                          <span className="flex items-center gap-1 rounded-full bg-primary/20 px-2 py-0.5 text-[10px] font-bold text-primary">
                            <Radio size={10} aria-hidden="true" />
                            LIVE
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-foreground/50">
                        {event.start_at ? new Date(event.start_at).toLocaleDateString(undefined, {
                          weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
                        }) : '—'}
                        {event.end_at && (
                          <> — {new Date(event.end_at).toLocaleDateString(undefined, {
                            weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
                          })}</>
                        )}
                      </p>
                    </div>
                    {isAdmin && !isActive && (
                      <button
                        onClick={() => setActiveEvent(event.id)}
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
            })}
          </div>
        )}
      </main>
    </div>
  );
}
