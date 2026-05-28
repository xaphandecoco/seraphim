import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { FileText, ArrowLeft } from 'lucide-react';
import { api } from '@/services/api';
import { useNavigate } from 'react-router-dom';

interface LogEntry {
  id: number;
  timestamp: string;
  action: string;
  matched_name: string | null;
  confidence: number | null;
  tier: string | null;
  camera_id: number | null;
  volunteer_id: number | null;
}

interface PaginatedLogs {
  items: LogEntry[];
  total: number;
  page: number;
  page_size: number;
}

async function fetchLogs(page: number = 1): Promise<PaginatedLogs> {
  const res = await api.get('/logs', { params: { page, page_size: 50 } });
  return res.data;
}

export function LogsPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);

  const { data, isLoading } = useQuery({
    queryKey: ['logs', page],
    queryFn: () => fetchLogs(page),
  });

  const logs = data?.items || [];
  const total = data?.total || 0;
  const totalPages = Math.ceil(total / 50);

  return (
    <div className="flex h-screen flex-col pb-20">
      <header className="border-b border-[#E8DDA8] bg-white/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/')} className="text-[#1F2128]/50 hover:text-[#1F2128]">
            <ArrowLeft size={20} />
          </button>
          <h1 className="text-lg font-bold text-[#1F2128]">System Logs</h1>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {isLoading ? (
          <div className="py-16 text-center text-sm text-[#1F2128]/50">Loading...</div>
        ) : logs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-[#1F2128]/50">
            <FileText size={40} className="mb-3 opacity-40" />
            <p className="text-sm font-medium">No logs found</p>
          </div>
        ) : (
          <div className="space-y-2 pb-4">
            {logs.map((log) => (
              <div key={log.id} className="rounded-2xl border border-[#E8DDA8] bg-white p-3 shadow-sm">
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-sm font-semibold text-[#1F2128]">
                      {log.action}
                    </p>
                    <p className="text-xs text-[#1F2128]/50">
                      {log.matched_name || 'Unknown'} {log.confidence ? `(${log.confidence})` : ''}
                    </p>
                    <p className="text-xs text-[#1F2128]/50">
                      {new Date(log.timestamp).toLocaleString()}
                    </p>
                  </div>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                    log.tier === '100' ? 'bg-green-100 text-green-700' :
                    log.tier === '91-99' ? 'bg-amber-100 text-amber-700' :
                    log.tier === 'below90' ? 'bg-red-100 text-red-600' :
                    'bg-gray-100 text-gray-600'
                  }`}>
                    {log.tier || 'unknown'}
                  </span>
                </div>
                {log.camera_id && (
                  <p className="mt-1 text-xs text-[#1F2128]/50">
                    Camera {log.camera_id}
                  </p>
                )}
              </div>
            ))}

            {totalPages > 1 && (
              <div className="flex items-center justify-center gap-2 pt-4">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  className="rounded-xl bg-[#FBF8F0] px-3 py-1.5 text-sm font-medium text-[#1F2128] border border-[#E8DDA8] transition-all hover:bg-[#F5D547]/20 disabled:opacity-50"
                >
                  Prev
                </button>
                <span className="text-sm text-[#1F2128]/50">
                  Page {page} of {totalPages}
                </span>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                  className="rounded-xl bg-[#FBF8F0] px-3 py-1.5 text-sm font-medium text-[#1F2128] border border-[#E8DDA8] transition-all hover:bg-[#F5D547]/20 disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
