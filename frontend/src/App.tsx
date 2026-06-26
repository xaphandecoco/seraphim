import { Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { useEffect, lazy, Suspense } from 'react';
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
import { EventDetailPage } from '@/pages/EventDetailPage';
import { EventSeriesPage } from '@/pages/EventSeriesPage';
import { NameMatchReviewPage } from '@/pages/NameMatchReviewPage';
import { CommunityReportsPage } from '@/pages/CommunityReportsPage';
import { CommunityReportFormPage } from '@/pages/CommunityReportFormPage';
import { CommunityReportDetailPage } from '@/pages/CommunityReportDetailPage';
import { MigrationPage } from '@/pages/MigrationPage';
import { MigrationReportPage } from '@/pages/MigrationReportPage';
import { FROrphanReviewPage } from '@/pages/FROrphanReviewPage';
import { RetentionReport } from '@/pages/RetentionReport';
import { AdvancedSearchPage } from '@/pages/AdvancedSearchPage';
import { SavedSearchesPage } from '@/pages/SavedSearchesPage';
import { GroupsPage } from '@/pages/GroupsPage';
import { GroupDetailPage } from '@/pages/GroupDetailPage';
import { VolunteerRoute } from '@/components/layout/VolunteerRoute';

// S13 — lazy-load Profiles pages (admin-only, rarely first load) + public WelcomePage
const ProfilesPage = lazy(() =>
  import('@/pages/ProfilesPage').then((m) => ({ default: m.ProfilesPage })),
);
const ProfileFormPage = lazy(() =>
  import('@/pages/ProfileFormPage').then((m) => ({ default: m.ProfileFormPage })),
);
const WelcomePage = lazy(() =>
  import('@/pages/WelcomePage').then((m) => ({ default: m.WelcomePage })),
);

// S16 — lazy-load TanStack-Query-heavy pages to keep App.tsx initial bundle light
const SystemStatusPage = lazy(() =>
  import('@/pages/SystemStatusPage').then((m) => ({ default: m.SystemStatusPage })),
);
const JobRunsPage = lazy(() =>
  import('@/pages/JobRunsPage').then((m) => ({ default: m.JobRunsPage })),
);

// S10 — lazy-load import wizard pages (data-heavy; volunteer+ only)
const ImportsListPage = lazy(() =>
  import('@/pages/ImportsListPage').then((m) => ({ default: m.ImportsListPage })),
);

// S11 — lazy-load duplicates page (volunteer+)
const DuplicatesPage = lazy(() =>
  import('@/pages/DuplicatesPage').then((m) => ({ default: m.DuplicatesPage })),
);

// S12 — lazy-load Activities/Tasks page (TanStack-Query-heavy; all authenticated users)
const ActivitiesPage = lazy(() =>
  import('@/pages/ActivitiesPage').then((m) => ({ default: m.ActivitiesPage })),
);
const ImportWizardPage = lazy(() =>
  import('@/pages/ImportWizardPage').then((m) => ({ default: m.ImportWizardPage })),
);
const ImportReportPage = lazy(() =>
  import('@/pages/ImportReportPage').then((m) => ({ default: m.ImportReportPage })),
);

function PageFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div
        className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-primary"
        aria-label="Loading"
      />
    </div>
  );
}

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
        {/* S13 — public newcomer form (no auth, no BottomNav) */}
        <Route path="/welcome" element={<Suspense fallback={<PageFallback />}><WelcomePage /></Suspense>} />
        <Route path="/" element={<ProtectedRoute><TasksPage /></ProtectedRoute>} />
        <Route path="/ranking" element={<ProtectedRoute><RankingPage /></ProtectedRoute>} />
        <Route path="/events" element={<ProtectedRoute><EventsPage /></ProtectedRoute>} />
        <Route path="/events/:id" element={<ProtectedRoute><EventDetailPage /></ProtectedRoute>} />
        {/* S15: gate contacts from viewer */}
        <Route path="/contacts" element={<ProtectedRoute><ContactsPage /></ProtectedRoute>} />
        <Route path="/contacts/search" element={<ProtectedRoute><AdvancedSearchPage /></ProtectedRoute>} />
        <Route path="/contacts/new" element={<ProtectedRoute><ContactFormPage mode="create" /></ProtectedRoute>} />
        <Route path="/contacts/:id" element={<ProtectedRoute><ContactDetailPage /></ProtectedRoute>} />
        <Route path="/contacts/:id/edit" element={<ProtectedRoute><ContactFormPage mode="edit" /></ProtectedRoute>} />
        <Route path="/searches" element={<ProtectedRoute><SavedSearchesPage /></ProtectedRoute>} />
        <Route path="/groups" element={<ProtectedRoute><GroupsPage /></ProtectedRoute>} />
        <Route path="/groups/:id" element={<ProtectedRoute><GroupDetailPage /></ProtectedRoute>} />
        <Route path="/attendees" element={<Navigate to="/contacts" replace />} />
        <Route path="/activities" element={<ProtectedRoute><Suspense fallback={<PageFallback />}><ActivitiesPage /></Suspense></ProtectedRoute>} />
        <Route path="/audit" element={<ProtectedRoute><AuditPage /></ProtectedRoute>} />
        {/* S13 — Profiles (admin-only) */}
        <Route path="/profiles" element={<AdminRoute><Suspense fallback={<PageFallback />}><ProfilesPage /></Suspense></AdminRoute>} />
        <Route path="/profiles/new" element={<AdminRoute><Suspense fallback={<PageFallback />}><ProfileFormPage /></Suspense></AdminRoute>} />
        <Route path="/profiles/:id/edit" element={<AdminRoute><Suspense fallback={<PageFallback />}><ProfileFormPage /></Suspense></AdminRoute>} />
        <Route path="/settings" element={<AdminRoute><SettingsPage /></AdminRoute>} />
        <Route path="/settings/users" element={<AdminRoute><UserManagementPage /></AdminRoute>} />
        <Route path="/settings/custom-fields" element={<AdminRoute><CustomFieldsPage /></AdminRoute>} />
        <Route path="/settings/attendance" element={<AdminRoute><AttendancePage /></AdminRoute>} />
        <Route path="/settings/event-series" element={<AdminRoute><EventSeriesPage /></AdminRoute>} />
        <Route path="/dashboard" element={<AdminRoute><DashboardPage /></AdminRoute>} />
        <Route path="/pit" element={<AdminRoute><PitPage /></AdminRoute>} />
        <Route path="/logs" element={<AdminRoute><LogsPage /></AdminRoute>} />
        <Route path="/bulk-upload" element={<AdminRoute><BulkPhotoUploadPage /></AdminRoute>} />
        <Route path="/name-match/review" element={<AdminRoute><NameMatchReviewPage /></AdminRoute>} />
        <Route path="/community-reports" element={<ProtectedRoute><CommunityReportsPage /></ProtectedRoute>} />
        <Route path="/community-reports/new" element={<ProtectedRoute><CommunityReportFormPage /></ProtectedRoute>} />
        <Route path="/community-reports/:id" element={<ProtectedRoute><CommunityReportDetailPage /></ProtectedRoute>} />
        <Route path="/settings/migration" element={<AdminRoute><MigrationPage /></AdminRoute>} />
        <Route path="/settings/migration/:batchId" element={<AdminRoute><MigrationReportPage /></AdminRoute>} />
        <Route path="/settings/fr-transition/orphans" element={<AdminRoute><FROrphanReviewPage /></AdminRoute>} />
        <Route path="/settings/system-status" element={<AdminRoute><Suspense fallback={<PageFallback />}><SystemStatusPage /></Suspense></AdminRoute>} />
        <Route path="/settings/jobs" element={<AdminRoute><Suspense fallback={<PageFallback />}><JobRunsPage /></Suspense></AdminRoute>} />
        <Route path="/settings/biometric" element={<AdminRoute><RetentionReport /></AdminRoute>} />
        {/* S10 — Import Wizard (volunteer+) */}
        <Route path="/imports" element={<VolunteerRoute><Suspense fallback={<PageFallback />}><ImportsListPage /></Suspense></VolunteerRoute>} />
        {/* S11 — Find & Merge Duplicates (volunteer+) */}
        <Route path="/duplicates" element={<VolunteerRoute><Suspense fallback={<PageFallback />}><DuplicatesPage /></Suspense></VolunteerRoute>} />
        <Route path="/imports/new" element={<VolunteerRoute><Suspense fallback={<PageFallback />}><ImportWizardPage /></Suspense></VolunteerRoute>} />
        <Route path="/imports/:batchId" element={<VolunteerRoute><Suspense fallback={<PageFallback />}><ImportReportPage /></Suspense></VolunteerRoute>} />
      </Routes>
    </div>
  );
}

export default App;
