import { Link, useLocation } from 'react-router-dom';
import { ClipboardList, Trophy, Calendar, Users, Settings, AlertTriangle, FileText } from 'lucide-react';
import { useAuthStore } from '@/store/authStore';
import { useTaskStore } from '@/store/taskStore';

function PendingBadge() {
  const pendingCount = useTaskStore((s) => s.pendingCount);
  if (pendingCount === 0) return null;
  return (
    <span className="absolute -right-2 -top-1 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
      {pendingCount > 99 ? '99+' : pendingCount}
    </span>
  );
}

export function BottomNav() {
  const location = useLocation();
  const isAdmin = useAuthStore((s) => s.isAdmin);

  const tabs = [
    { path: '/', label: 'Tasks', icon: ClipboardList },
    { path: '/ranking', label: 'Ranking', icon: Trophy },
    { path: '/events', label: 'Events', icon: Calendar },
    { path: '/attendees', label: 'Attendees', icon: Users },
  ];

  if (isAdmin) {
    tabs.push({ path: '/pit', label: 'Pit', icon: AlertTriangle });
    tabs.push({ path: '/logs', label: 'Logs', icon: FileText });
    tabs.push({ path: '/settings', label: 'Settings', icon: Settings });
  }

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 border-t border-[#E8DDA8] bg-white/95 backdrop-blur-sm">
      <div className="mx-auto flex max-w-md items-center justify-around">
        {tabs.map((tab) => {
          const isActive = location.pathname === tab.path;
          const Icon = tab.icon;
          return (
            <Link
              key={tab.path}
              to={tab.path}
              className={`relative flex flex-1 flex-col items-center justify-center py-2 transition-colors ${
                isActive
                  ? 'text-[#F5D547]'
                  : 'text-[#1F2128]/40 hover:text-[#1F2128]/70'
              }`}
            >
              <div className="relative">
                <Icon size={22} strokeWidth={isActive ? 2.5 : 2} />
                {tab.path === '/' && <PendingBadge />}
              </div>
              <span className="mt-0.5 text-[10px] font-semibold">{tab.label}</span>
              {isActive && (
                <span className="absolute top-0 h-0.5 w-8 rounded-full bg-[#F5D547]" />
              )}
            </Link>
          );
        })}
      </div>
      {/* Safe area padding for mobile devices */}
      <div className="h-[env(safe-area-inset-bottom)]" />
    </nav>
  );
}
