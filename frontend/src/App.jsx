import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext.jsx";
import { ToastProvider } from "./context/ToastContext.jsx";
import { RequireAuth, RequireGuest, RequireAdmin } from "./routes/Guards.jsx";
import Shell from "./layout/Shell.jsx";

import Login from "./pages/auth/Login.jsx";
import ForgotPassword from "./pages/auth/ForgotPassword.jsx";
import ResetPassword from "./pages/auth/ResetPassword.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Users from "./pages/settings/Users.jsx";
import MLTemplates from "./pages/settings/MLTemplates.jsx";
import DbtMacros from "./pages/settings/DbtMacros.jsx";
import DqFlagRegistry from "./pages/settings/DqFlagRegistry.jsx";
import ServersList from "./pages/servers/ServersList.jsx";
import ServerDetail from "./pages/servers/ServerDetail.jsx";
import OrchestratorsList from "./pages/orchestrators/OrchestratorsList.jsx";
import OrchestratorDetail from "./pages/orchestrators/OrchestratorDetail.jsx";
import SourcesList from "./pages/sources/SourcesList.jsx";
import ImportsList from "./pages/imports/ImportsList.jsx";
import ProjectsList from "./pages/medallion/ProjectsList.jsx";
import ProjectDetail from "./pages/medallion/ProjectDetail.jsx";
import AdminOverview from "./pages/medallion/AdminOverview.jsx";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <Routes>
            <Route element={<RequireGuest />}>
              <Route path="/login" element={<Login />} />
              <Route path="/forgot-password" element={<ForgotPassword />} />
              <Route path="/reset-password" element={<ResetPassword />} />
            </Route>

            <Route element={<RequireAuth />}>
              <Route element={<Shell />}>
                <Route path="/" element={<Dashboard />} />
                <Route path="/servers" element={<ServersList />} />
                <Route path="/servers/:id" element={<ServerDetail />} />
                <Route path="/orchestrators" element={<OrchestratorsList />} />
                <Route path="/orchestrators/:id" element={<OrchestratorDetail />} />
                <Route path="/sources" element={<SourcesList />} />
                <Route path="/imports" element={<ImportsList />} />
                <Route path="/medallion" element={<ProjectsList />} />
                <Route path="/medallion/:id" element={<ProjectDetail />} />
                <Route element={<RequireAdmin />}>
                  <Route path="/medallion/overview" element={<AdminOverview />} />
                  <Route path="/medallion/overview/:id" element={<ProjectDetail readOnly />} />
                  <Route path="/settings/users" element={<Users />} />
                  <Route path="/settings/ml-templates" element={<MLTemplates />} />
                  <Route path="/settings/dbt-macros" element={<DbtMacros />} />
                  <Route path="/settings/dq-flag-registry" element={<DqFlagRegistry />} />
                </Route>
              </Route>
            </Route>

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
