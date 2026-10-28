import { useState, type FormEvent } from "react";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import { Alert } from "../components/FormParts";
import { AsyncView, Note, PageHeader } from "../components/ui";
import { Panel } from "../dashboard/Panel";
import {
  ApiError, createUser, listAdminUsers, listBanks, listInstitutions, updateUser,
  type AdminUser, type InstitutionOut, type UserUpdateInput,
} from "../lib/api";
import { adminRevokeSessions } from "../lib/api";
import { formatDate, formatDateTime } from "../lib/format";
import { ROLE_LABELS, roleLabel } from "../lib/roles";
import { useAsync } from "../lib/useAsync";
import { NotPermitted } from "./FdrCreate";

const PAGE_SIZE = 50;
const FIELD = "mt-1 block w-full rounded border border-slate-300 bg-white px-3 py-1.5 text-sm";

/** User-facing text for a failed administration request: the server's own explanation where it gives one. */
export function describeAdminError(err: unknown): string {
  if (!(err instanceof ApiError)) return "The request failed unexpectedly. Please try again.";
  if (err.kind === "network") return "Cannot reach the server. Nothing was changed.";
  if (err.kind === "forbidden") return "You are not permitted to do this, or the request was blocked by a security check.";
  if (err.kind === "server") return `The server reported an error (HTTP ${err.status}).`;
  if (Array.isArray(err.detail)) {
    const first = (err.detail as Array<{ msg?: string }>)[0]?.msg ?? "The request was not valid";
    return first.replace(/^Value error, /, "");
  }
  return err.message;
}

function institutionName(list: InstitutionOut[], id: string | null): string {
  if (!id) return "—";
  const found = list.find((i) => i.id === id);
  return found ? `${found.name} (${found.code})` : id;
}

function RoleSelect({ id, value, onChange }: { id: string; value: string; onChange: (v: string) => void }) {
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={FIELD}>
      {Object.entries(ROLE_LABELS).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
    </select>
  );
}

function InstitutionSelect({ id, value, onChange, list }: { id: string; value: string; onChange: (v: string) => void; list: InstitutionOut[] }) {
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={FIELD}>
      <option value="">Choose an institution</option>
      {list.map((i) => <option key={i.id} value={i.id}>{i.name} ({i.code})</option>)}
    </select>
  );
}

function CreateUser({ institutions, onCreated }: { institutions: InstitutionOut[]; onCreated: () => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("INSTITUTION_USER");
  const [institution, setInstitution] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ email: string; password: string } | null>(null);
  const needsInstitution = role === "INSTITUTION_USER";

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await createUser({ email: email.trim(), full_name: name.trim(), role, institution_id: needsInstitution ? institution : null });
      setCreated({ email: result.user.email, password: result.temporary_password });
      setEmail(""); setName(""); setInstitution("");
      onCreated();
    } catch (err) {
      setError(describeAdminError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Create a user" description="There is no self-registration. The new user must change the temporary password at first sign-in.">
      {created && (
        <div role="status" className="mb-4 space-y-2 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
          <p>User <strong>{created.email}</strong> was created. Give them this temporary password securely. <strong>It is shown only now</strong> and cannot be retrieved later.</p>
          <p className="font-mono text-base" data-testid="temporary-password">{created.password}</p>
          <button type="button" onClick={() => setCreated(null)} className="rounded border border-amber-400 px-3 py-1 text-xs font-medium">I have noted it: dismiss</button>
        </div>
      )}
      {error && <div className="mb-3"><Alert tone="error">{error}</Alert></div>}
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2" aria-label="Create a user">
        <div>
          <label htmlFor="new-email" className="block text-xs font-medium text-slate-700">Email</label>
          <input id="new-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={FIELD} />
        </div>
        <div>
          <label htmlFor="new-name" className="block text-xs font-medium text-slate-700">Full name</label>
          <input id="new-name" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} className={FIELD} />
        </div>
        <div>
          <label htmlFor="new-role" className="block text-xs font-medium text-slate-700">Role</label>
          <RoleSelect id="new-role" value={role} onChange={setRole} />
        </div>
        {needsInstitution && (
          <div>
            <label htmlFor="new-institution" className="block text-xs font-medium text-slate-700">Institution (required for this role)</label>
            <InstitutionSelect id="new-institution" value={institution} onChange={setInstitution} list={institutions} />
          </div>
        )}
        <div className="sm:col-span-2">
          <button type="submit" disabled={busy || !email.trim() || !name.trim() || (needsInstitution && !institution)}
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:bg-slate-500">
            {busy ? "Creating…" : "Create user"}
          </button>
        </div>
      </form>
    </Panel>
  );
}

function EditUser({ user, institutions, isSelf, onDone }: { user: AdminUser; institutions: InstitutionOut[]; isSelf: boolean; onDone: (changed: boolean) => void }) {
  const [name, setName] = useState(user.full_name);
  const [role, setRole] = useState(user.role);
  const [institution, setInstitution] = useState(user.institution_id ?? "");
  const [active, setActive] = useState(user.is_active);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const patch: UserUpdateInput = {};
  if (name.trim() !== user.full_name) patch.full_name = name.trim();
  if (role !== user.role) patch.role = role;
  if (role === "INSTITUTION_USER" && institution !== (user.institution_id ?? "")) patch.institution_id = institution;
  if (active !== user.is_active) patch.is_active = active;
  const changed = Object.keys(patch).length > 0;
  const risky = patch.role !== undefined || patch.is_active === false;
  const invalid = role === "INSTITUTION_USER" && !institution;

  async function save() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await updateUser(user.id, patch);
      onDone(true);
    } catch (err) {
      setError(describeAdminError(err));
      setConfirming(false);
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded border border-slate-300 bg-slate-50 p-3" role="group" aria-label={`Edit ${user.email}`}>
      {error && <Alert tone="error">{error}</Alert>}
      {isSelf && <Note>This is your own account. The server refuses changes that would leave the system without an active administrator.</Note>}
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`name-${user.id}`} className="block text-xs font-medium text-slate-700">Full name</label>
          <input id={`name-${user.id}`} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} className={FIELD} />
        </div>
        <div>
          <label htmlFor={`role-${user.id}`} className="block text-xs font-medium text-slate-700">Role</label>
          <RoleSelect id={`role-${user.id}`} value={role} onChange={setRole} />
        </div>
        {role === "INSTITUTION_USER" && (
          <div>
            <label htmlFor={`inst-${user.id}`} className="block text-xs font-medium text-slate-700">Institution</label>
            <InstitutionSelect id={`inst-${user.id}`} value={institution} onChange={setInstitution} list={institutions} />
          </div>
        )}
        <div className="flex items-end">
          <label className="flex items-center gap-2 text-sm text-slate-800">
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
            Account is active
          </label>
        </div>
      </div>
      {confirming ? (
        <div role="alertdialog" aria-label="Confirm account change" className="rounded border border-slate-900 bg-white p-3 text-sm">
          <p>
            Apply these changes to <strong>{user.email}</strong>?{" "}
            {patch.is_active === false && "Deactivating signs them out everywhere immediately. "}
            {patch.role !== undefined && "A role change takes effect on their next request. "}
            Each change is recorded in the audit trail under your name.
          </p>
          <div className="mt-3 flex gap-3">
            <button type="button" onClick={save} disabled={busy} className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white disabled:bg-slate-500">{busy ? "Saving…" : "Confirm changes"}</button>
            <button type="button" onClick={() => setConfirming(false)} disabled={busy} className="rounded border border-slate-300 px-4 py-1.5 text-sm">Cancel</button>
          </div>
        </div>
      ) : (
        <div className="flex gap-3">
          <button type="button" disabled={!changed || invalid || busy} onClick={() => (risky ? setConfirming(true) : void save())}
            className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white disabled:bg-slate-500">Save changes</button>
          <button type="button" onClick={() => onDone(false)} className="rounded border border-slate-300 px-4 py-1.5 text-sm">Close</button>
        </div>
      )}
    </div>
  );
}

/** Ends every session of a user so they must sign in again. A second click confirms; the server enforces it at once. */
function EndSessions({ user, isSelf }: { user: { id: string; email: string }; isSelf: boolean }) {
  const [step, setStep] = useState<"idle" | "confirm">("idle");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  async function run() {
    setBusy(true);
    try {
      const result = await adminRevokeSessions(user.id);
      setMessage({ tone: "ok", text: result.revoked === 0 ? "They had no active sessions." : `${result.revoked} ${result.revoked === 1 ? "session" : "sessions"} ended.` });
    } catch {
      setMessage({ tone: "error", text: "The sessions could not be ended. Please try again." });
    } finally {
      setBusy(false);
      setStep("idle");
    }
  }
  return (
    <span className="ml-2 inline-flex flex-col align-top">
      {step === "confirm" ? (
        <span role="group" aria-label={`Confirm ending all sessions of ${user.email}`} className="flex items-center gap-2">
          <button type="button" disabled={busy} onClick={run} className="rounded bg-red-800 px-3 py-1 text-xs font-medium text-white hover:bg-red-900 disabled:opacity-50">
            {isSelf ? "Confirm: sign me out everywhere" : "Confirm: end all sessions"}
          </button>
          <button type="button" disabled={busy} onClick={() => setStep("idle")} className="rounded border border-slate-300 px-3 py-1 text-xs font-medium hover:bg-slate-100">Cancel</button>
        </span>
      ) : (
        <button type="button" aria-label={`End all sessions of ${user.email}`} onClick={() => { setMessage(null); setStep("confirm"); }} className="rounded border border-slate-300 px-3 py-1 text-xs font-medium hover:bg-slate-100">
          End sessions
        </button>
      )}
      {message && <span role={message.tone === "error" ? "alert" : "status"} className={`mt-1 text-xs ${message.tone === "error" ? "text-red-800" : "text-emerald-900"}`}>{message.text}</span>}
    </span>
  );
}

function Users({ institutions }: { institutions: InstitutionOut[] }) {
  const { state: auth } = useAuth();
  const selfId = auth.status === "authenticated" ? auth.user.id : "";
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<string | null>(null);
  const { state, reload } = useAsync((signal) => listAdminUsers({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }, signal), page);
  return (
    <div className="space-y-6">
      <CreateUser institutions={institutions} onCreated={reload} />
      <Panel title="Users">
        <AsyncView state={state} reload={reload} what="users">
          {(data) => (
            <>
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <caption className="sr-only">User accounts</caption>
                  <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th scope="col" className="py-2 pr-4 font-medium">User</th>
                      <th scope="col" className="py-2 pr-4 font-medium">Role</th>
                      <th scope="col" className="py-2 pr-4 font-medium">Institution</th>
                      <th scope="col" className="py-2 pr-4 font-medium">Account</th>
                      <th scope="col" className="py-2 pr-4 font-medium">Last sign-in</th>
                      <th scope="col" className="py-2 font-medium"><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {data.items.map((u) => (
                      <tr key={u.id} className="align-top">
                        <th scope="row" className="py-2 pr-4 font-medium text-slate-900">{u.full_name}<span className="block text-xs font-normal text-slate-500">{u.email}</span></th>
                        <td className="py-2 pr-4">{roleLabel(u.role)}</td>
                        <td className="py-2 pr-4 text-slate-700">{institutionName(institutions, u.institution_id)}</td>
                        <td className="py-2 pr-4">
                          {u.is_active ? "Active" : "Deactivated"}
                          {u.is_locked && <span className="block text-xs text-amber-900">Locked after failed sign-ins</span>}
                          {u.must_change_password && <span className="block text-xs text-slate-600">Must change password</span>}
                        </td>
                        <td className="whitespace-nowrap py-2 pr-4 text-slate-700">{u.last_login_at ? formatDateTime(u.last_login_at) : "Never"}</td>
                        <td className="py-2">
                          <button type="button" aria-label={`Edit ${u.email}`} onClick={() => setEditing(editing === u.id ? null : u.id)} className="rounded border border-slate-300 px-3 py-1 text-xs font-medium hover:bg-slate-100">
                            {editing === u.id ? "Close" : "Edit"}
                          </button>
                          <EndSessions user={u} isSelf={u.id === selfId} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {editing && data.items.find((u) => u.id === editing) && (
                <div className="mt-4">
                  <EditUser key={editing} user={data.items.find((u) => u.id === editing)!} institutions={institutions} isSelf={editing === selfId}
                    onDone={(changed) => { setEditing(null); if (changed) reload(); }} />
                </div>
              )}
              <div className="mt-4 flex items-center justify-between text-xs text-slate-600">
                <span>{data.total.toLocaleString("en-IN")} {data.total === 1 ? "user" : "users"}</span>
                {data.total > PAGE_SIZE && (
                  <span className="flex items-center gap-2">
                    <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded border border-slate-300 px-3 py-1 font-medium disabled:opacity-50">Previous</button>
                    Page {page} of {Math.ceil(data.total / PAGE_SIZE)}
                    <button type="button" disabled={page * PAGE_SIZE >= data.total} onClick={() => setPage(page + 1)} className="rounded border border-slate-300 px-3 py-1 font-medium disabled:opacity-50">Next</button>
                  </span>
                )}
              </div>
            </>
          )}
        </AsyncView>
      </Panel>
    </div>
  );
}

function Institutions({ list }: { list: InstitutionOut[] }) {
  return (
    <Panel title="Institutions">
      <Note>Institution records are read-only in this version: the API offers no way to create or change them, and none is faked here.</Note>
      <table className="mt-3 min-w-full text-left text-sm">
        <caption className="sr-only">Institutions</caption>
        <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
          <tr><th scope="col" className="py-2 pr-4 font-medium">Code</th><th scope="col" className="py-2 pr-4 font-medium">Name</th><th scope="col" className="py-2 pr-4 font-medium">State</th><th scope="col" className="py-2 pr-4 font-medium">Type</th><th scope="col" className="py-2 font-medium">Added</th></tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {list.map((i) => (
            <tr key={i.id}><th scope="row" className="py-2 pr-4 font-medium">{i.code}</th><td className="py-2 pr-4">{i.name}</td><td className="py-2 pr-4">{i.state}</td><td className="py-2 pr-4">{i.institution_type}</td><td className="py-2">{formatDate(i.created_at)}</td></tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function Banks() {
  const { state, reload } = useAsync((signal) => listBanks({ page_size: 500 }, signal));
  return (
    <Panel title="Banks">
      <Note>Bank records are read-only in this version: the API offers no way to create or change them.</Note>
      <div className="mt-3">
        <AsyncView state={state} reload={reload} what="banks">
          {(data) => (
            <table className="min-w-full text-left text-sm">
              <caption className="sr-only">Banks</caption>
              <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                <tr><th scope="col" className="py-2 pr-4 font-medium">Code</th><th scope="col" className="py-2 font-medium">Name</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.items.map((b) => <tr key={b.id}><th scope="row" className="py-2 pr-4 font-medium">{b.code}</th><td className="py-2">{b.name}</td></tr>)}
              </tbody>
            </table>
          )}
        </AsyncView>
      </div>
    </Panel>
  );
}

type Tab = "users" | "institutions" | "banks";

function AdminContent() {
  const [tab, setTab] = useState<Tab>("users");
  const institutions = useAsync((signal) => listInstitutions({ limit: 200 }, signal));
  const tabs: Array<[Tab, string]> = [["users", "Users"], ["institutions", "Institutions"], ["banks", "Banks"]];
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader title="Administration">Manage who can use the portal. Every change is recorded in the audit trail under your name.</PageHeader>
      <DemoNotice />
      <div role="tablist" aria-label="Administration sections" className="flex gap-2 border-b border-slate-200">
        {tabs.map(([key, label]) => (
          <button key={key} role="tab" type="button" aria-selected={tab === key} onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${tab === key ? "border-slate-900 text-slate-900" : "border-transparent text-slate-600 hover:text-slate-900"}`}>
            {label}
          </button>
        ))}
      </div>
      <AsyncView state={institutions.state} reload={institutions.reload} what="institutions">
        {(data) => (tab === "users" ? <Users institutions={data.items} /> : tab === "institutions" ? <Institutions list={data.items} /> : <Banks />)}
      </AsyncView>
    </div>
  );
}

export default function Admin() {
  const { state: auth } = useAuth();
  return auth.status === "authenticated" && auth.user.role === "ADMIN" ? <AdminContent /> : <NotPermitted what="Administration" />;
}
