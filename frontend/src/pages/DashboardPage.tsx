import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts';
import { ArrowLeft, Download } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/services/api';

const TIER_COLORS: Record<string, string> = {
  '100': '#22c55e',
  '91-99': '#f59e0b',
  'below90': '#ef4444',
  'unknown': '#9ca3af',
};

function downloadCsv(url: string, filename: string) {
  api.get(url, { responseType: 'blob' })
    .then((res) => {
      const blob = new Blob([res.data], { type: 'text/csv' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      link.click();
    })
    .catch(() => toast.error('Export failed'));
}

export function DashboardPage() {
  const navigate = useNavigate();

  const { data: attendanceData = [] } = useQuery({
    queryKey: ['analytics-attendance'],
    queryFn: () => api.get('/analytics/attendance-by-event').then((r) => r.data),
  });

  const { data: tierData = [] } = useQuery({
    queryKey: ['analytics-tiers'],
    queryFn: () => api.get('/analytics/tier-distribution').then((r) => r.data),
  });

  const { data: volunteerData = [] } = useQuery({
    queryKey: ['analytics-volunteers'],
    queryFn: () => api.get('/analytics/volunteer-stats').then((r) => r.data),
  });

  const { data: queueData = [] } = useQuery({
    queryKey: ['analytics-queue'],
    queryFn: () => api.get('/analytics/queue-health').then((r) => r.data),
  });

  return (
    <div className="flex h-screen flex-col pb-20">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button onClick={() => navigate('/settings')} aria-label="Back" className="text-foreground/50 hover:text-foreground">
              <ArrowLeft size={20} aria-hidden="true" />
            </button>
            <h1 className="text-lg font-bold text-foreground">Analytics Dashboard</h1>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => downloadCsv('/analytics/export/attendance', 'attendance.csv')}
              aria-label="Export attendance as CSV"
              className="flex items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-primary/10"
            >
              <Download size={13} aria-hidden="true" />
              Attendance
            </button>
            <button
              onClick={() => downloadCsv('/analytics/export/logs', 'logs.csv')}
              aria-label="Export logs as CSV"
              className="flex items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-primary/10"
            >
              <Download size={13} aria-hidden="true" />
              Logs
            </button>
          </div>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto space-y-4 px-3 pt-3 pb-4">

        {/* Attendance by event */}
        <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-bold text-foreground">Attendance per Event</h2>
          {attendanceData.length === 0 ? (
            <p className="text-xs text-foreground/40">No data yet</p>
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={attendanceData} margin={{ top: 0, right: 8, bottom: 24, left: 0 }}>
                <XAxis
                  dataKey="title"
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v: string) => v.length > 14 ? v.slice(0, 14) + '…' : v}
                  angle={-25}
                  textAnchor="end"
                />
                <YAxis tick={{ fontSize: 10 }} width={28} />
                <Tooltip
                  formatter={(v: number) => [v, 'Attendees']}
                  contentStyle={{ fontSize: 12, borderRadius: 8 }}
                />
                <Bar dataKey="count" fill="hsl(48 90% 62%)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Daily queue health */}
        <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-bold text-foreground">Daily Detections (14 days)</h2>
          {queueData.length === 0 ? (
            <p className="text-xs text-foreground/40">No data yet</p>
          ) : (
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={queueData} margin={{ top: 0, right: 8, bottom: 20, left: 0 }}>
                <XAxis
                  dataKey="day"
                  tick={{ fontSize: 9 }}
                  tickFormatter={(v: string) => v.slice(5)}
                  angle={-25}
                  textAnchor="end"
                />
                <YAxis tick={{ fontSize: 10 }} width={28} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                <Bar dataKey="total" name="Total" fill="hsl(220 9% 70%)" radius={[4, 4, 0, 0]} />
                <Bar dataKey="resolved" name="Resolved" fill="hsl(48 90% 62%)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Tier distribution */}
        <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-bold text-foreground">Recognition Tier Distribution</h2>
          {tierData.length === 0 ? (
            <p className="text-xs text-foreground/40">No data yet</p>
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <PieChart>
                <Pie
                  data={tierData}
                  dataKey="count"
                  nameKey="tier"
                  cx="50%"
                  cy="50%"
                  outerRadius={70}
                  label={({ tier, percent }) => `${tier} ${(percent * 100).toFixed(0)}%`}
                  labelLine={false}
                >
                  {tierData.map((entry: { tier: string }) => (
                    <Cell key={entry.tier} fill={TIER_COLORS[entry.tier] || '#9ca3af'} />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Volunteer stats */}
        <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-bold text-foreground">Volunteer Performance</h2>
          {volunteerData.length === 0 ? (
            <p className="text-xs text-foreground/40">No data yet</p>
          ) : (
            <div className="space-y-2">
              {volunteerData.map((v: { volunteer_id: number; name: string; confirmed: number; edited: number; added: number; points: number }) => (
                <div key={v.volunteer_id} className="flex items-center justify-between rounded-xl bg-background px-3 py-2">
                  <div>
                    <p className="text-sm font-semibold text-foreground">{v.name}</p>
                    <p className="text-[10px] text-foreground/50">
                      {v.confirmed} confirmed · {v.edited} edited · {v.added} added
                    </p>
                  </div>
                  <span className="text-sm font-bold text-primary">{v.points} pts</span>
                </div>
              ))}
            </div>
          )}
        </div>

      </main>
    </div>
  );
}
