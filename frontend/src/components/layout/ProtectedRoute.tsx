import { Navigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
import { BottomNav } from './BottomNav';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

export function ProtectedRoute({ children }: ProtectedRouteProps) {
  const token = useAuthStore((s) => s.token);
  const authReady = useAuthStore((s) => s.authReady);

  // Wait for the initial /auth/refresh attempt before deciding
  if (!authReady) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-primary" aria-label="Loading" />
      </div>
    );
  }

  if (!token) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div className="pb-16">
      {children}
      <BottomNav />
    </div>
  );
}
