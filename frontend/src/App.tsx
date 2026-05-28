import { Routes, Route, useNavigate } from 'react-router-dom';
import { useEffect } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { ProtectedRoute } from '@/components/layout/ProtectedRoute';
import { AdminRoute } from '@/components/layout/AdminRoute';
import { LoginPage } from '@/pages/LoginPage';
import { OAuthCallbackPage } from '@/pages/OAuthCallbackPage';
import { TasksPage } from '@/pages/TasksPage';
import { RankingPage } from '@/pages/RankingPage';
import { EventsPage } from '@/pages/EventsPage';
import { AttendeesPage } from '@/pages/AttendeesPage';
import { SettingsPage } from '@/pages/SettingsPage';
import { SetupPage } from '@/pages/SetupPage';
import { PitPage } from '@/pages/PitPage';
import { LogsPage } from '@/pages/LogsPage';

function AuthInit() {
  useAuth();
  return null;
}

function SetupCheck() {
  const navigate = useNavigate();
  
  useEffect(() => {
    // Check if setup is needed
    fetch('/api/setup/status')
      .then(res => res.json())
      .then(data => {
        if (!data.setup_complete && window.location.pathname !== '/setup') {
          navigate('/setup');
        }
      })
      .catch(() => {
        // If setup endpoint fails, we might need setup
      });
  }, [navigate]);
  
  return null;
}

function App() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <AuthInit />
      <SetupCheck />
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/auth/callback" element={<OAuthCallbackPage />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <TasksPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/ranking"
          element={
            <ProtectedRoute>
              <RankingPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/events"
          element={
            <ProtectedRoute>
              <EventsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/attendees"
          element={
            <ProtectedRoute>
              <AttendeesPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/settings"
          element={
            <AdminRoute>
              <SettingsPage />
            </AdminRoute>
          }
        />
        <Route
          path="/pit"
          element={
            <AdminRoute>
              <PitPage />
            </AdminRoute>
          }
        />
        <Route
          path="/logs"
          element={
            <AdminRoute>
              <LogsPage />
            </AdminRoute>
          }
        />
      </Routes>
    </div>
  );
}

export default App;
