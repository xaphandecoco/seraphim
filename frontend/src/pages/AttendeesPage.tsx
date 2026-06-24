import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Users, Search, User } from 'lucide-react';
import { api } from '@/services/api';
import type { Attendee } from '@/types';

async function fetchAttendees(search: string = ''): Promise<Attendee[]> {
  const res = await api.get('/members/attendees', { params: { search, limit: 200 } });
  return res.data;
}

export function AttendeesPage() {
  const [search, setSearch] = useState('');

  const { data: attendees = [], isLoading } = useQuery({
    queryKey: ['attendees', search],
    queryFn: () => fetchAttendees(search),
  });

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-bold text-foreground">Attendees</h1>
        </div>
      </header>

      {/* Search bar */}
      <div className="px-3 pt-3">
        <div className="relative">
          <Search
            size={16}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-foreground/40"
          />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or nickname..."
            className="h-10 w-full rounded-xl border border-border bg-card pl-9 pr-3 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
          />
        </div>
      </div>

      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {isLoading ? (
          <div className="py-16 text-center text-sm text-foreground/50">
            Loading...
          </div>
        ) : attendees.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-foreground/50">
            <Users size={40} className="mb-3 opacity-40" />
            <p className="text-sm font-medium">No attendees found</p>
            <p className="mt-1 text-xs">Try a different search term</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 pb-4 sm:grid-cols-3 md:grid-cols-4">
            {attendees.map((attendee) => {
              const imageUrl = attendee.face_thumbnail_path
                ? `/api${attendee.face_thumbnail_path}`
                : undefined;

              return (
                <div
                  key={attendee.contact_id}
                  className="flex flex-col items-center rounded-2xl border border-border bg-card p-3 shadow-sm"
                >
                  {/* Face thumbnail */}
                  <div className="shrink-0">
                    {imageUrl ? (
                      <img
                        src={imageUrl}
                        alt={`${attendee.first_name} ${attendee.last_name}`}
                        className="h-24 w-24 rounded-xl object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <div className="flex h-24 w-24 items-center justify-center rounded-xl bg-background">
                        <User size={32} className="text-foreground/30" />
                      </div>
                    )}
                  </div>

                  {/* Info */}
                  <div className="mt-2 w-full text-center">
                    <p className="truncate text-sm font-bold text-foreground">
                      {attendee.first_name} {attendee.last_name}
                    </p>
                    {attendee.nickname && (
                      <p className="truncate text-xs text-foreground/50">
                        "{attendee.nickname}"
                      </p>
                    )}
                    <p className="mt-1 text-[10px] text-foreground/40">
                      ID: {attendee.contact_id}
                    </p>
                    {attendee.sample_count > 0 && (
                      <p className="text-[10px] font-semibold text-primary">
                        {attendee.sample_count} sample{attendee.sample_count === 1 ? '' : 's'}
                      </p>
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
