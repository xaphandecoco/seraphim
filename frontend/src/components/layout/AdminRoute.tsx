import { Navigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
import { BottomNav } from './BottomNav';

interface AdminRouteProps {
  children: React.ReactNode;
}

export function AdminRoute({ children }: AdminRouteProps) {
  const token = useAuthStore((s) => s.token);
  const isAdmin = useAuthStore((s) => s.isAdmin);

  if (!token) {
    return <Navigate to="/login" replace />;
  }

  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="pb-16">
      {children}
      <BottomNav />
    </div>
  );
}
