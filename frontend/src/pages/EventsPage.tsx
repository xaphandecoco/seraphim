import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Calendar, CalendarDays, RefreshCw } from 'lucide-react';
import { api } from '@/services/api';
import { useAuthStore } from '@/store/authStore';
import type { ChurchEvent } from '@/types';

async function fetchEvents(): Promise<ChurchEvent[]> {
  const res = await api.get('/events');
  return res.data;
}

export function EventsPage() {
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const queryClient = useQueryClient();
  const [syncing, setSyncing] = useState(false);

  const { data: events = [], isLoading } = useQuery({
    queryKey: ['events'],
    queryFn: fetchEvents,
  });

  const syncEvents = async () => {
    setSyncing(true);
    try {
      await api.post('/events/sync');
      await queryClient.invalidateQueries({ queryKey: ['events'] });
      toast.success('Events synced from CiviCRM');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Sync failed');
    } finally {
      setSyncing(false);
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
              aria-label="Sync events from CiviCRM"
              className="flex items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground transition-all hover:bg-primary/10 disabled:opacity-50"
            >
              <RefreshCw size={13} className={syncing ? 'animate-spin' : ''} aria-hidden="true" />
              {syncing ? 'Syncing…' : 'Sync'}
            </button>
          )}
        </div>
      </header>
      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {isLoading ? (
          <div className="py-16 text-center text-sm text-foreground/50">Loading…</div>
        ) : events.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-foreground/50">
            <CalendarDays size={40} className="mb-3 opacity-40" aria-hidden="true" />
            <p className="text-sm font-medium">No upcoming events</p>
            {isAdmin && <p className="mt-1 text-xs">Use Sync to pull events from CiviCRM</p>}
          </div>
        ) : (
          <div className="space-y-2 pb-4">
            {events.map((event) => (
              <div
                key={event.event_id}
                className="rounded-2xl border border-border bg-card p-4 shadow-sm"
              >
                <div className="flex items-start gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/20">
                    <Calendar size={18} className="text-primary" aria-hidden="true" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <h3 className="text-sm font-bold text-foreground">{event.title}</h3>
                    <p className="mt-1 text-xs text-foreground/50">
                      {new Date(event.start_date).toLocaleDateString(undefined, {
                        weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
                      })}
                      {event.end_date && (
                        <> — {new Date(event.end_date).toLocaleDateString(undefined, {
                          weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
                        })}</>
                      )}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
