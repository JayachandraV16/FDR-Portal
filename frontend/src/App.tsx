import { Route, Routes } from "react-router-dom";

import { RequireCurrentPassword, RequireSession } from "./auth/guards";
import AppShell from "./components/AppShell";
import Admin from "./pages/Admin";
import BulkImport from "./pages/BulkImport";
import Calendar from "./pages/Calendar";
import Alerts from "./pages/Alerts";
import ChangePassword from "./pages/ChangePassword";
import Dashboard from "./pages/Dashboard";
import Documents, { DocumentsDetail } from "./pages/Documents";
import Exceptions from "./pages/Exceptions";
import FdrCreate from "./pages/FdrCreate";
import FdrDetail from "./pages/FdrDetail";
import FdrEdit from "./pages/FdrEdit";
import FdrRegistry from "./pages/FdrRegistry";
import Institutions from "./pages/Institutions";
import Login from "./pages/Login";
import Release, { ReleaseDetail } from "./pages/Release";
import Reports from "./pages/Reports";
import Security from "./pages/Security";
import Verification, { VerificationDetail } from "./pages/Verification";
import Workload from "./pages/Workload";
import ReviewDetail from "./pages/ReviewDetail";
import ReviewQueue from "./pages/ReviewQueue";
import NotFound from "./pages/NotFound";

export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/login" element={<Login />} />

      {/* Signed-in users only */}
      <Route element={<RequireSession />}>
        <Route path="/change-password" element={<ChangePassword />} />
        {/* ...and only once any temporary password has been replaced */}
        <Route element={<RequireCurrentPassword />}>
          <Route element={<AppShell />}>
            <Route index element={<Dashboard />} />
            <Route path="fdrs" element={<FdrRegistry />} />
            <Route path="fdrs/new" element={<FdrCreate />} />
            <Route path="import" element={<BulkImport />} />
            <Route path="fdrs/:id" element={<FdrDetail />} />
            <Route path="fdrs/:id/edit" element={<FdrEdit />} />
            <Route path="calendar" element={<Calendar />} />
            <Route path="workload" element={<Workload />} />
            <Route path="review" element={<ReviewQueue />} />
            <Route path="review/:id" element={<ReviewDetail />} />
            <Route path="institutions" element={<Institutions />} />
            <Route path="documents" element={<Documents />} />
            <Route path="documents/:id" element={<DocumentsDetail />} />
            <Route path="verification" element={<Verification />} />
            <Route path="verification/:id" element={<VerificationDetail />} />
            <Route path="alerts" element={<Alerts />} />
            <Route path="exceptions" element={<Exceptions />} />
            <Route path="release" element={<Release />} />
            <Route path="release/:id" element={<ReleaseDetail />} />
            <Route path="reports" element={<Reports />} />
            <Route path="security" element={<Security />} />
            <Route path="admin" element={<Admin />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Route>
      </Route>
    </Routes>
  );
}
