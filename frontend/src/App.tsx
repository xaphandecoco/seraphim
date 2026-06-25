import { Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { useEffect } from 'react';
import { Toaster } from 'sonner';
import { useAuth } from '@/hooks/useAuth';
import { ProtectedRoute } from '@/components/layout/ProtectedRoute';
import { AdminRoute } from '@/components/layout/AdminRoute';
import { LoginPage } from '@/pages/LoginPage';
import { OAuthCallbackPage } from '@/pages/OAuthCallbackPage';
import { TasksPage } from '@/pages/TasksPage';
import { RankingPage } from '@/pages/RankingPage';
import { EventsPage } from '@/pages/EventsPage';
import { ContactsPage } from '@/pages/ContactsPage';
import { ContactDetailPage } from '@/pages/ContactDetailPage';
import { ContactFormPage } from '@/pages/ContactFormPage';
import { SettingsPage } from '@/pages/SettingsPage';
import { SetupPage } from '@/pages/SetupPage';
import { PitPage } from '@/pages/PitPage';
import { LogsPage } from '@/pages/LogsPage';
import { UserManagementPage } from '@/pages/UserManagementPage';
import { AttendancePage } from '@/pages/AttendancePage';
import { CustomFieldsPage } from '@/pages/CustomFieldsPage';
import { DashboardPage } from '@/pages/DashboardPage';
import { AuditPage } from '@/pages/AuditPage';
import { BulkPhotoUploadPage } from '@/pages/BulkPhotoUploadPage';

function AuthInit() {
  useAuth();
  return null;
}

function SetupCheck() {
  const navigate = useNavigate();

  useEffect(() => {
    fetch('/api/setup/status')
      .then((res) => res.json())
      .then((data) => {
        if (!data.setup_complete && window.location.pathname !== '/setup') {
          navigate('/setup');
        }
      })
      .catch(() => {});
  }, [navigate]);

  return null;
}

function App() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <Toaster position="top-center" richColors />
      <AuthInit />
      <SetupCheck />
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/auth/callback" element={<OAuthCallbackPage />} />
        <Route path="/" element={<ProtectedRoute><TasksPage /></ProtectedRoute>} />
        <Route path="/ranking" element={<ProtectedRoute><RankingPage /></ProtectedRoute>} />
        <Route path="/events" element={<ProtectedRoute><EventsPage /></ProtectedRoute>} />
        {/* S15: gate contacts from viewer */}
        <Route path="/contacts" element={<ProtectedRoute><ContactsPage /></ProtectedRoute>} />
        <Route path="/contacts/new" element={<ProtectedRoute><ContactFormPage mode="create" /></ProtectedRoute>} />
        <Route path="/contacts/:id" element={<ProtectedRoute><ContactDetailPage /></ProtectedRoute>} />
        <Route path="/contacts/:id/edit" element={<ProtectedRoute><ContactFormPage mode="edit" /></ProtectedRoute>} />
        <Route path="/attendees" element={<Navigate to="/contacts" replace />} />
        <Route path="/audit" element={<ProtectedRoute><AuditPage /></ProtectedRoute>} />
        <Route path="/settings" element={<AdminRoute><SettingsPage /></AdminRoute>} />
        <Route path="/settings/users" element={<AdminRoute><UserManagementPage /></AdminRoute>} />
        <Route path="/settings/custom-fields" element={<AdminRoute><CustomFieldsPage /></AdminRoute>} />
        <Route path="/settings/attendance" element={<AdminRoute><AttendancePage /></AdminRoute>} />
        <Route path="/dashboard" element={<AdminRoute><DashboardPage /></AdminRoute>} />
        <Route path="/pit" element={<AdminRoute><PitPage /></AdminRoute>} />
        <Route path="/logs" element={<AdminRoute><LogsPage /></AdminRoute>} />
        <Route path="/bulk-upload" element={<AdminRoute><BulkPhotoUploadPage /></AdminRoute>} />
      </Routes>
    </div>
  );
}

export default App;
