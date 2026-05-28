import { useQuery } from '@tanstack/react-query';
import { Trophy, Medal } from 'lucide-react';
import { api } from '@/services/api';
import type { LeaderboardEntry } from '@/types';

async function fetchLeaderboard(): Promise<LeaderboardEntry[]> {
  const res = await api.get('/leaderboard');
  return res.data;
}

export function RankingPage() {
  const { data: entries = [], isLoading } = useQuery({
    queryKey: ['leaderboard'],
    queryFn: fetchLeaderboard,
  });

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-[#E8DDA8] bg-white/95 px-4 py-3 backdrop-blur-sm">
        <h1 className="text-lg font-bold text-[#1F2128]">Leaderboard</h1>
      </header>
      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {isLoading ? (
          <div className="py-16 text-center text-sm text-[#1F2128]/50">
            Loading...
          </div>
        ) : entries.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-[#1F2128]/50">
            <Trophy size={40} className="mb-3 opacity-40" />
            <p className="text-sm font-medium">No rankings yet</p>
            <p className="mt-1 text-xs">Complete tasks to earn points</p>
          </div>
        ) : (
          <div className="space-y-2 pb-4">
            {entries.map((entry, index) => (
              <div
                key={entry.volunteer_email}
                className="flex items-center gap-3 rounded-2xl border border-[#E8DDA8] bg-white p-3 shadow-sm"
              >
                <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-bold ${
                  index === 0 ? 'bg-[#F5D547]/20 text-[#F5D547]' :
                  index === 1 ? 'bg-gray-100 text-gray-500' :
                  index === 2 ? 'bg-amber-50 text-amber-600' :
                  'bg-[#FBF8F0] text-[#1F2128]/50'
                }`}>
                  {index === 0 ? (
                    <Medal size={16} className="text-[#F5D547]" />
                  ) : index === 1 ? (
                    <Medal size={16} className="text-gray-400" />
                  ) : index === 2 ? (
                    <Medal size={16} className="text-amber-600" />
                  ) : (
                    index + 1
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-[#1F2128]">
                    {entry.volunteer_email}
                  </p>
                </div>
                <span className="shrink-0 text-sm font-bold text-[#F5D547]">
                  {entry.total_points} pts
                </span>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
