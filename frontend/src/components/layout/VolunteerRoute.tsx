import { Navigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
import { BottomNav } from './BottomNav';

interface VolunteerRouteProps {
  children: React.ReactNode;
}

/**
 * Route guard for volunteer+ pages.
 * - Unauthenticated → /login
 * - Viewer (isVolunteer=false) → /
 * - Volunteer or admin → renders children wrapped in BottomNav
 */
export function VolunteerRoute({ children }: VolunteerRouteProps) {
  const token = useAuthStore((s) => s.token);
  const isVolunteer = useAuthStore((s) => s.isVolunteer);
  const authReady = useAuthStore((s) => s.authReady);

  if (!authReady) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div
          className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-primary"
          aria-label="Loading"
        />
      </div>
    );
  }

  if (!token) {
    return <Navigate to="/login" replace />;
  }

  if (!isVolunteer) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="pb-16">
      {children}
      <BottomNav />
    </div>
  );
}
