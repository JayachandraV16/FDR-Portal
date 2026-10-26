import { render } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";

import App from "../App";
import { AuthProvider } from "../auth/AuthContext";

export function LocationDisplay() {
  const { pathname } = useLocation();
  return <span data-testid="location">{pathname}</span>;
}

/** The real application (routes, guards, provider) at a given URL, with no backend. */
export function renderApp(initialPath = "/", { strict = false }: { strict?: boolean } = {}) {
  const tree = (
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <App />
        <LocationDisplay />
      </AuthProvider>
    </MemoryRouter>
  );
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree);
}

/** Just the provider, for testing the context through a small probe component. */
export function renderWithAuth(ui: ReactNode, { strict = false }: { strict?: boolean } = {}) {
  const tree = (
    <MemoryRouter>
      <AuthProvider>{ui}</AuthProvider>
    </MemoryRouter>
  );
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree);
}
