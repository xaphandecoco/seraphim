import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ClipboardList, Trophy, Calendar, CalendarRange, Users, Settings, Settings2, AlertTriangle, FileText, ShieldCheck, MoreHorizontal, X, Upload, UserCheck, FileCheck, Database, Fingerprint } from 'lucide-react';
import { useAuthStore } from '@/store/authStore';
import { useTaskStore } from '@/store/taskStore';

function PendingBadge({ count }: { count: number }) {
  return (
    <span
      aria-label={`${count} pending task${count !== 1 ? 's' : ''}`}
      className="absolute -right-2 -top-1 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white"
    >
      {count > 99 ? '99+' : count}
    </span>
  );
}

export function BottomNav() {
  const location = useLocation();
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const pendingCount = useTaskStore((s) => s.pendingCount);
  const [showMore, setShowMore] = useState(false);

  const mainTabs = [
    { path: '/', label: 'Tasks', icon: ClipboardList },
    { path: '/audit', label: 'Audit', icon: ShieldCheck },
    { path: '/ranking', label: 'Ranking', icon: Trophy },
    { path: '/events', label: 'Events', icon: Calendar },
    { path: '/contacts', label: 'Contacts', icon: Users },
  ];

  const adminTabs = [
    { path: '/pit', label: 'Pit Queue', icon: AlertTriangle },
    { path: '/bulk-upload', label: 'Bulk Upload', icon: Upload },
    { path: '/logs', label: 'System Logs', icon: FileText },
    { path: '/settings', label: 'Settings', icon: Settings },
    { path: '/settings/custom-fields', label: 'Custom Fields', icon: Settings2 },
    { path: '/settings/event-series', label: 'Event Series', icon: CalendarRange },
    { path: '/name-match/review', label: 'Name Review', icon: UserCheck },
    { path: '/community-reports', label: 'Reports', icon: FileCheck },
    { path: '/settings/migration', label: 'Migration', icon: Database },
    { path: '/settings/biometric', label: 'Biometric', icon: Fingerprint },
  ];

  return (
    <>
      {/* Admin "More" sheet */}
      {isAdmin && showMore && (
        <div className="fixed inset-0 z-40" onClick={() => setShowMore(false)}>
          <div
            className="absolute bottom-16 left-0 right-0 mx-auto max-w-md rounded-t-2xl border border-border bg-card p-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <p className="text-xs font-bold uppercase tracking-wide text-foreground/50">Admin</p>
              <button onClick={() => setShowMore(false)} aria-label="Close" className="text-foreground/40 hover:text-foreground">
                <X size={16} aria-hidden="true" />
              </button>
            </div>
            <div className="space-y-1">
              {adminTabs.map((tab) => {
                const Icon = tab.icon;
                const isActive = location.pathname === tab.path;
                return (
                  <Link
                    key={tab.path}
                    to={tab.path}
                    onClick={() => setShowMore(false)}
                    className={`flex items-center gap-3 rounded-xl px-4 py-3 transition-colors ${isActive ? 'bg-primary/10 text-primary' : 'text-foreground hover:bg-background'}`}
                  >
                    <Icon size={18} aria-hidden="true" />
                    <span className="text-sm font-semibold">{tab.label}</span>
                  </Link>
                );
              })}
            </div>
          </div>
        </div>
      )}

      <nav
        aria-label="Main navigation"
        className="fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-card/95 backdrop-blur-sm"
      >
        <div className="mx-auto flex max-w-md items-center justify-around">
          {mainTabs.map((tab) => {
            const isActive = location.pathname === tab.path;
            const Icon = tab.icon;
            return (
              <Link
                key={tab.path}
                to={tab.path}
                aria-label={tab.label}
                aria-current={isActive ? 'page' : undefined}
                className={`relative flex flex-1 flex-col items-center justify-center py-2 transition-colors ${
                  isActive ? 'text-primary' : 'text-foreground/40 hover:text-foreground/70'
                }`}
              >
                <div className="relative">
                  <Icon size={22} strokeWidth={isActive ? 2.5 : 2} aria-hidden="true" />
                  {tab.path === '/' && pendingCount > 0 && <PendingBadge count={pendingCount} />}
                </div>
                <span className="mt-0.5 text-[10px] font-semibold" aria-hidden="true">{tab.label}</span>
                {isActive && <span className="absolute top-0 h-0.5 w-8 rounded-full bg-primary" aria-hidden="true" />}
              </Link>
            );
          })}

          {isAdmin && (
            <button
              onClick={() => setShowMore(!showMore)}
              aria-label="Admin menu"
              aria-expanded={showMore}
              className={`relative flex flex-1 flex-col items-center justify-center py-2 transition-colors ${
                adminTabs.some((t) => location.pathname === t.path) ? 'text-primary' : 'text-foreground/40 hover:text-foreground/70'
              }`}
            >
              <MoreHorizontal size={22} aria-hidden="true" />
              <span className="mt-0.5 text-[10px] font-semibold" aria-hidden="true">More</span>
            </button>
          )}
        </div>
        <div className="h-[env(safe-area-inset-bottom)]" aria-hidden="true" />
      </nav>
    </>
  );
}
