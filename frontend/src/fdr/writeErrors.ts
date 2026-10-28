import { ApiError, fieldErrorsFrom } from "../lib/api";
import { FORM_FIELDS } from "./validation";
import type { FieldErrors } from "./FdrForm";

export interface WriteFailure {
  fields: FieldErrors;
  message: string | null; // shown above the form when it is not about one field
  stale: boolean; // 409 "changed since you loaded it": offer a reload
}

/** Turns a failed create/edit/submit call into field messages plus one general message. The backend's
 * own wording is shown for validation and conflict errors; transport and server failures get generic
 * text so nothing internal reaches the page. */
export function describeWriteFailure(err: unknown): WriteFailure {
  if (!(err instanceof ApiError)) return { fields: {}, message: "The request failed unexpectedly. Please try again.", stale: false };
  switch (err.kind) {
    case "network":
      return { fields: {}, message: "Cannot reach the server. Check your connection and try again.", stale: false };
    case "server":
      return { fields: {}, message: `The server reported an error (HTTP ${err.status}). Please try again shortly.`, stale: false };
    case "forbidden":
      return { fields: {}, message: "You are not permitted to do this, or the request was blocked by a security check.", stale: false };
    case "validation": {
      const fields = fieldErrorsFrom(err, FORM_FIELDS) as FieldErrors;
      const general = fields._form ?? (Object.keys(fields).length === 0 ? err.message : null);
      return { fields, message: general, stale: false };
    }
    default:
      return { fields: {}, message: err.message, stale: err.status === 409 && /changed since you loaded/i.test(err.message) };
  }
}
