import type { AuthUser } from "../lib/api";
import { listInstitutions } from "../lib/api";
import { roleLabel } from "../lib/roles";
import { useAsync } from "../lib/useAsync";

function InstitutionContext({ user }: { user: AuthUser }) {
  // An institution user is scoped by the backend to one institution, so the first (only) row is theirs.
  const { state } = useAsync((signal) => listInstitutions({ limit: 1 }, signal));
  if (state.status === "loading") return <span>Institution: loading…</span>;
  if (state.status === "error") return <span>Institution: unavailable</span>;
  const institution = state.data.items[0];
  return institution ? (
    <span>
      Institution: {institution.name} ({institution.code})
    </span>
  ) : (
    <span>Institution: not found (account reference {user.institution_id})</span>
  );
}

export default function Welcome({ user }: { user: AuthUser }) {
  return (
    <div>
      <h1 className="text-2xl font-semibold text-slate-900">Overview</h1>
      <p className="mt-1 text-sm text-slate-700">
        Welcome, <span className="font-medium">{user.full_name}</span>
      </p>
      <p className="mt-0.5 flex flex-wrap gap-x-4 text-xs text-slate-500">
        <span>Role: {roleLabel(user.role)}</span>
        {user.role === "INSTITUTION_USER" ? <InstitutionContext user={user} /> : <span>Scope: all institutions</span>}
      </p>
    </div>
  );
}
