import { useQuery } from '@tanstack/react-query';
import { Calendar, CalendarDays } from 'lucide-react';
import { api } from '@/services/api';
import type { ChurchEvent } from '@/types';

async function fetchEvents(): Promise<ChurchEvent[]> {
  const res = await api.get('/events');
  return res.data;
}

export function EventsPage() {
  const { data: events = [], isLoading } = useQuery({
    queryKey: ['events'],
    queryFn: fetchEvents,
  });

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-[#E8DDA8] bg-white/95 px-4 py-3 backdrop-blur-sm">
        <h1 className="text-lg font-bold text-[#1F2128]">Events</h1>
      </header>
      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {isLoading ? (
          <div className="py-16 text-center text-sm text-[#1F2128]/50">
            Loading...
          </div>
        ) : events.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-[#1F2128]/50">
            <CalendarDays size={40} className="mb-3 opacity-40" />
            <p className="text-sm font-medium">No upcoming events</p>
          </div>
        ) : (
          <div className="space-y-2 pb-4">
            {events.map((event) => (
              <div
                key={event.event_id}
                className="rounded-2xl border border-[#E8DDA8] bg-white p-4 shadow-sm"
              >
                <div className="flex items-start gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#F5D547]/20">
                    <Calendar size={18} className="text-[#F5D547]" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <h3 className="text-sm font-bold text-[#1F2128]">
                      {event.title}
                    </h3>
                    <p className="mt-1 text-xs text-[#1F2128]/50">
                      {new Date(event.start_date).toLocaleDateString(undefined, {
                        weekday: 'short',
                        month: 'short',
                        day: 'numeric',
                        year: 'numeric',
                      })}
                      {event.end_date && (
                        <>
                          {' '}
                          —{' '}
                          {new Date(event.end_date).toLocaleDateString(undefined, {
                            weekday: 'short',
                            month: 'short',
                            day: 'numeric',
                            year: 'numeric',
                          })}
                        </>
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
