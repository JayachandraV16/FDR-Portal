import { Route, Routes } from "react-router-dom";

import { RequireCurrentPassword, RequireSession } from "./auth/guards";
import AppShell from "./components/AppShell";
import ChangePassword from "./pages/ChangePassword";
import Dashboard from "./pages/Dashboard";
import FdrRegistry from "./pages/FdrRegistry";
import Institutions from "./pages/Institutions";
import Login from "./pages/Login";
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
            <Route path="institutions" element={<Institutions />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Route>
      </Route>
    </Routes>
  );
}
