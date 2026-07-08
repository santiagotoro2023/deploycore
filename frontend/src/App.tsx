import { ReactElement, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import Layout from "./components/Layout";
import { AuthProvider, useAuth } from "./state/auth";
import { OrgProvider } from "./state/org";

import AuditLog from "./pages/AuditLog";
import Dashboard from "./pages/Dashboard";
import DeploymentDetail from "./pages/DeploymentDetail";
import Deployments from "./pages/Deployments";
import DeploymentWizard from "./pages/DeploymentWizard";
import DiskLayouts from "./pages/DiskLayouts";
import Hypervisors from "./pages/Hypervisors";
import IsoAssets from "./pages/IsoAssets";
import Login from "./pages/Login";
import Organizations from "./pages/Organizations";
import SettingsPage from "./pages/Settings";
import SetupWizard from "./pages/SetupWizard";
import Templates from "./pages/Templates";
import Users from "./pages/Users";

function RequireAuth({ children }: { children: ReactElement }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-8 text-sm text-neutral-500">Loading...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="organizations" element={<Organizations />} />
        <Route path="deployments" element={<Deployments />} />
        <Route path="deployments/new" element={<DeploymentWizard />} />
        <Route path="deployments/:id" element={<DeploymentDetail />} />
        <Route path="templates" element={<Templates />} />
        <Route path="disk-layouts" element={<DiskLayouts />} />
        <Route path="hypervisors" element={<Hypervisors />} />
        <Route path="iso-assets" element={<IsoAssets />} />
        <Route path="users" element={<Users />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="audit-log" element={<AuditLog />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);

  useEffect(() => {
    api
      .get<{ needs_setup: boolean }>("/setup/status")
      .then((r) => setNeedsSetup(r.needs_setup))
      .catch(() => setNeedsSetup(false));
  }, []);

  if (needsSetup === null) return <div className="p-8 text-sm text-neutral-500">Loading...</div>;
  if (needsSetup) return <SetupWizard onComplete={() => setNeedsSetup(false)} />;

  return (
    <AuthProvider>
      <OrgProvider>
        <AppRoutes />
      </OrgProvider>
    </AuthProvider>
  );
}
