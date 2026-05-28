import { Navigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
import { BottomNav } from './BottomNav';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

export function ProtectedRoute({ children }: ProtectedRouteProps) {
  const token = useAuthStore((s) => s.token);

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
