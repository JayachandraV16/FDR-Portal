import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link, matchPath, useLocation } from "react-router-dom";

import { ApiError, getChatSuggestions, sendChat, sendFeedback, type ChatLanguage, type ChatPage, type ChatReply, type FeedbackReason } from "../lib/api";
import { CHAT_TEXT, LANGUAGE_NAMES, loadLanguage, saveLanguage, type ChatText } from "../lib/chatText";
import { useOnline } from "../lib/useOnline";
import { newId } from "../lib/uuid";
import { Icon } from "./icons";

export const MAX_MESSAGE = 1000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const FALLBACK_SUGGESTIONS = ["How do I create an FDR?", "Which documents are required?", "What do the FDR statuses mean?"];

// A page is the first matching prefix; a record page (with the record's id) has its own name so the help matches it.
const PAGE_OF: Array<[string, ChatPage]> = [
  ["/fdrs", "fdrs"], ["/documents", "documents"], ["/verification", "verification"], ["/review", "review"],
  ["/alerts", "alerts"], ["/exceptions", "exceptions"], ["/release", "release"], ["/reports", "reports"],
  ["/calendar", "calendar"], ["/workload", "workload"], ["/security", "security"], ["/import", "import"], ["/admin", "admin"],
];
const RECORD_ROUTES: Array<[string, ChatPage]> = [
  ["/fdrs/:id/edit", "fdr_edit"], ["/fdrs/:id", "fdr_detail"], ["/review/:id", "review_detail"],
  ["/verification/:id", "verification_detail"], ["/documents/:id", "documents_detail"], ["/release/:id", "release_detail"],
];

/** Which page the user is on, and which FDR they are looking at (if any). Only used to choose suggestions, to pick
 * the page help and to resolve "this FDR"; the server still checks that the user may see the record. */
export function contextFor(pathname: string): { page: ChatPage; fdr_id?: string } {
  if (pathname === "/fdrs/new") return { page: "fdr_new" };
  for (const [pattern, page] of RECORD_ROUTES) {
    const id = matchPath(pattern, pathname)?.params.id;
    if (id && UUID.test(id)) return { page, fdr_id: id };
  }
  const page: ChatPage = pathname === "/" ? "overview" : (PAGE_OF.find(([prefix]) => pathname === prefix || pathname.startsWith(prefix + "/"))?.[1] ?? "default");
  return { page };
}

interface Turn {
  id: number;
  role: "user" | "assistant";
  text: string;
  reply?: ChatReply;
  language?: ChatLanguage;
  replyId?: string; // identifies this answer when it is rated
  page?: ChatPage;
}

function describeFailure(err: unknown, t: ChatText): string {
  if (err instanceof ApiError) {
    if (err.kind === "network") return t.networkError;
    if (err.status === 429) return err.message;
    if (err.status === 422) return err.message && typeof err.detail === "string" ? err.message : t.tooLong;
    if (err.kind === "server") return t.serverError(err.status);
    if (err.kind === "forbidden") return t.sessionError;
  }
  return t.generic;
}

type FeedbackState =
  | { phase: "idle" }
  | { phase: "reason" }
  | { phase: "sending" }
  | { phase: "done" }
  | { phase: "unavailable" }
  | { phase: "error"; message: string };

const REASON_ORDER: FeedbackReason[] = ["incorrect", "unclear", "incomplete", "not_relevant", "other"];

/** Thumbs up / down for one answer. Sends only facts about the answer and a fixed reason; once recorded it cannot be
 * sent again, and while a request is running a second one is ignored. */
function Feedback({ turn, t }: { turn: Turn; t: ChatText }) {
  const [state, setState] = useState<FeedbackState>({ phase: "idle" });
  const [reason, setReason] = useState<FeedbackReason | "">("");
  const sending = useRef(false);
  const reply = turn.reply;
  if (!reply || !turn.replyId) return null;

  async function submit(rating: "helpful" | "not_helpful", chosen?: FeedbackReason) {
    if (sending.current || !reply || !turn.replyId) return;
    sending.current = true;
    setState({ phase: "sending" });
    const provider = reply.provider === "gemini" || reply.provider === "openai_compatible" ? reply.provider : undefined;
    try {
      await sendFeedback({
        reply_id: turn.replyId, rating, ...(chosen ? { reason: chosen } : {}), kind: reply.kind, source: reply.source,
        ...(reply.source === "llm" && provider ? { provider } : {}), language: reply.language ?? "en", ...(turn.page ? { page: turn.page } : {}),
      });
      setState({ phase: "done" });
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) setState({ phase: "unavailable" });
      else if (err instanceof ApiError && err.status === 429) setState({ phase: "error", message: err.message });
      else setState({ phase: "error", message: t.feedbackError });
    } finally {
      sending.current = false;
    }
  }

  if (state.phase === "done") return <p role="status" className="flex items-center gap-1 text-xs text-emerald-900"><Icon name="check" className="h-3.5 w-3.5" />{t.thanks}</p>;
  if (state.phase === "unavailable") return <p role="status" className="text-xs text-slate-600">{t.feedbackUnavailable}</p>;
  const busy = state.phase === "sending";
  const button = "inline-flex items-center gap-1 rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50";
  return (
    <div className="space-y-1.5 border-t border-slate-100 pt-1.5">
      <div role="group" aria-label={t.helpfulQuestion} className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-slate-600">{t.helpfulQuestion}</span>
        <button type="button" disabled={busy} onClick={() => void submit("helpful")} className={button}><Icon name="thumbUp" className="h-3.5 w-3.5" />{t.helpful}</button>
        <button type="button" disabled={busy} aria-expanded={state.phase === "reason"} onClick={() => setState({ phase: "reason" })} className={button}><Icon name="thumbDown" className="h-3.5 w-3.5" />{t.notHelpful}</button>
      </div>
      {state.phase === "reason" || (state.phase === "error" && reason !== "") ? (
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-[11px] text-slate-700" htmlFor={`reason-${turn.replyId}`}>{t.reasonLabel}</label>
          <select id={`reason-${turn.replyId}`} value={reason} onChange={(e) => setReason(e.target.value as FeedbackReason | "")} className="rounded border border-slate-300 bg-white px-1.5 py-1 text-xs text-slate-900">
            <option value="">—</option>
            {REASON_ORDER.map((r) => <option key={r} value={r}>{t.reasons[r]}</option>)}
          </select>
          <button type="button" disabled={busy} onClick={() => void submit("not_helpful", reason || undefined)} className={button}>{t.submitFeedback}</button>
        </div>
      ) : null}
      {state.phase === "error" && <p role="alert" className="text-xs text-red-800">{state.message}</p>}
    </div>
  );
}

function AssistantBubble({ turn, t }: { turn: Turn; t: ChatText }) {
  const reply = turn.reply;
  const language = reply?.language ?? "en";
  return (
    <div className="max-w-[92%] space-y-2 rounded-lg rounded-tl-none border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800" data-role="assistant">
      <p lang={language} className="whitespace-pre-wrap break-words">{turn.text}</p>
      {reply && reply.facts.length > 0 && (
        <ul lang={language} className="space-y-0.5 rounded bg-slate-50 px-2 py-1.5 text-xs text-slate-700" aria-label={t.facts}>
          {reply.facts.map((f, i) => <li key={i}><span className="font-medium">{f.label}:</span> {f.value}</li>)}
        </ul>
      )}
      {reply && reply.links.length > 0 && (
        <p lang={language} className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {reply.links.map((l) => <Link key={l.path + l.label} to={l.path} className="font-medium text-slate-900 underline">{l.label}</Link>)}
        </p>
      )}
      {reply && (
        <p className="text-[11px] text-slate-500">
          {reply.source !== "llm" ? t.builtIn : reply.kind === "unknown" ? t.aiGuidance(reply.provider ?? "") : t.aiWording(reply.provider ?? "")}
          {reply.notice ? <span>{` · ${reply.notice}`}</span> : null}
        </p>
      )}
      <Feedback turn={turn} t={t} />
    </div>
  );
}

export default function ChatWidget() {
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const [language, setLanguage] = useState<ChatLanguage>(loadLanguage);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; retry: () => void } | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>(FALLBACK_SUGGESTIONS);
  const input = useRef<HTMLTextAreaElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const inFlight = useRef(false);
  const nextId = useRef(1);
  const online = useOnline();
  const t = CHAT_TEXT[language];
  const context = contextFor(pathname);

  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const controller = new AbortController();
    getChatSuggestions(context.page, language, controller.signal).then(
      (r) => { if (!controller.signal.aborted && r.suggestions.length) setSuggestions(r.suggestions); },
      () => undefined, // the built-in suggestions stay: a missing list must not look like an error
    );
    return () => controller.abort();
  }, [open, context.page, language]);

  useEffect(() => {
    log.current?.scrollTo?.({ top: log.current.scrollHeight });
  }, [turns, busy, error]);

  // The launcher is not mounted while the panel is open, so focus returns to it once it is back.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (!open && wasOpen.current) opener.current?.focus();
    wasOpen.current = open;
  }, [open]);

  function close() {
    setOpen(false);
  }

  function chooseLanguage(next: ChatLanguage) {
    setLanguage(next);
    saveLanguage(next);
    setSuggestions(FALLBACK_SUGGESTIONS); // replaced when the list for this language arrives; the conversation stays
  }

  /** Send a typed question or the page-explanation request. Both show up in the conversation and can be retried. */
  async function run(shown: string, request: () => Promise<ChatReply>, retry: () => void) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    setTurns((turnList) => [...turnList, { id: nextId.current++, role: "user", text: shown, language }]);
    try {
      const raw = await request();
      if (typeof raw?.reply !== "string" || raw.reply.trim() === "") throw new Error("empty reply");
      // Tolerate a reply that omits its optional lists rather than failing to render it.
      const reply: ChatReply = { ...raw, facts: raw.facts ?? [], links: raw.links ?? [], suggestions: raw.suggestions ?? [] };
      setTurns((turnList) => [...turnList, { id: nextId.current++, role: "assistant", text: reply.reply, reply, replyId: newId(), page: context.page }]);
      if (reply.suggestions.length) setSuggestions(reply.suggestions);
    } catch (err) {
      setError({ message: describeFailure(err, t), retry });
    } finally {
      inFlight.current = false;
      setBusy(false);
      input.current?.focus();
    }
  }

  function send(text: string) {
    const message = text.trim();
    if (!message || inFlight.current || !online) return;
    setDraft("");
    const body = { ...context, ...(language !== "en" ? { language } : {}) };
    void run(message, () => sendChat(message, body), () => send(message));
  }

  function explainPage() {
    if (inFlight.current || !online) return;
    const body = { ...context, intent: "explain_page" as const, ...(language !== "en" ? { language } : {}) };
    void run(t.explainSent, () => sendChat(t.explainSent, body), explainPage);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send(draft);
    } else if (event.key === "Escape") {
      close();
    }
  }

  function clear() {
    setTurns([]);
    setError(null);
    setDraft("");
    input.current?.focus();
  }

  if (!open) {
    return (
      <button
        ref={opener}
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t.launcher}
        className="fixed bottom-5 right-5 z-40 flex items-center gap-2 rounded-full bg-slate-900 px-4 py-3 text-sm font-medium text-white shadow-lg hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-400 focus:ring-offset-2"
      >
        <Icon name="copilot" className="h-5 w-5" /> FDR Copilot
      </button>
    );
  }

  const buttonClass = "rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-40";
  return (
    <section
      role="dialog"
      aria-label="FDR Copilot"
      lang={language}
      className="fixed inset-x-3 bottom-3 z-40 flex max-h-[80vh] flex-col overflow-hidden rounded-lg border border-slate-300 bg-slate-50 shadow-2xl sm:inset-x-auto sm:right-5 sm:bottom-5 sm:w-[26rem]"
    >
      <div className="flex items-start justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">{t.title}</h2>
          <p className="text-xs text-slate-500">{t.tagline}</p>
        </div>
        <div className="flex shrink-0 flex-col gap-2 sm:flex-row">
          <button type="button" onClick={clear} disabled={turns.length === 0 && !error} className={buttonClass}>{t.clear}</button>
          <button type="button" onClick={close} aria-label={`${t.close} ${t.title}`} className={buttonClass}>{t.close}</button>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-white px-4 py-2">
        <button type="button" onClick={explainPage} disabled={busy || !online} className={`${buttonClass} inline-flex items-center gap-1`}>
          <Icon name="help" className="h-4 w-4" /> {t.explain}
        </button>
        <label className="flex items-center gap-1.5 text-xs text-slate-700">
          <Icon name="globe" className="h-4 w-4" />
          <span className="sr-only sm:not-sr-only">{t.language}</span>
          <select
            value={language}
            onChange={(e) => chooseLanguage(e.target.value as ChatLanguage)}
            className="rounded border border-slate-300 bg-white px-1.5 py-1 text-xs text-slate-900"
          >
            {(Object.keys(LANGUAGE_NAMES) as ChatLanguage[]).map((code) => (
              <option key={code} value={code} lang={code}>{LANGUAGE_NAMES[code]}</option>
            ))}
          </select>
        </label>
      </div>

      <div ref={log} role="log" aria-live="polite" aria-label={t.conversation} className="min-h-[8rem] flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {turns.length === 0 && (
          <div className="space-y-2 text-sm text-slate-700">
            <p>{t.intro}</p>
          </div>
        )}
        {turns.map((turn) =>
          turn.role === "user" ? (
            <div key={turn.id} lang={turn.language} className="ml-auto max-w-[85%] whitespace-pre-wrap break-words rounded-lg rounded-tr-none bg-slate-900 px-3 py-2 text-sm text-white" data-role="user">
              {turn.text}
            </div>
          ) : (
            <AssistantBubble key={turn.id} turn={turn} t={t} />
          ),
        )}
        {busy && <p role="status" className="text-xs text-slate-500">{t.thinking}</p>}
        {!online && (
          <p role="status" className="flex items-center gap-1.5 rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
            <Icon name="offline" className="h-4 w-4" /> {t.offline}
          </p>
        )}
        {error && (
          <div role="alert" className="space-y-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            <p>{error.message}</p>
            <button type="button" onClick={error.retry} disabled={busy || !online} className="rounded border border-red-300 px-2 py-1 text-xs font-medium hover:bg-red-100 disabled:opacity-50">
              {t.tryAgain}
            </button>
          </div>
        )}
      </div>

      {turns.length === 0 && (
        <div className="flex flex-wrap gap-2 border-t border-slate-200 bg-white px-4 py-2" aria-label={t.suggested}>
          {suggestions.map((s) => (
            <button key={s} type="button" disabled={busy || !online} onClick={() => send(s)} className="rounded-full border border-slate-300 px-3 py-1 text-xs text-slate-800 hover:bg-slate-100 disabled:opacity-50">
              {s}
            </button>
          ))}
        </div>
      )}

      <form
        onSubmit={(e) => { e.preventDefault(); send(draft); }}
        className="border-t border-slate-200 bg-white p-3"
      >
        <label htmlFor="copilot-input" className="sr-only">{t.question}</label>
        <textarea
          id="copilot-input"
          ref={input}
          value={draft}
          onChange={(e) => setDraft(e.target.value.slice(0, MAX_MESSAGE))}
          onKeyDown={onKeyDown}
          rows={2}
          maxLength={MAX_MESSAGE}
          placeholder={t.placeholder}
          className="block w-full resize-none rounded border border-slate-300 px-3 py-2 text-sm focus:border-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-300"
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="text-[11px] text-slate-500">{draft.length}/{MAX_MESSAGE} · {t.keys}</span>
          <button type="submit" disabled={busy || !online || draft.trim() === ""} className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-slate-400">
            {busy ? t.sending : t.send}
          </button>
        </div>
      </form>
    </section>
  );
}
