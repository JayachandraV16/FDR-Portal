// Display labels for the backend's role codes (backend/app/models/user.py: Role).
export const ROLE_LABELS: Record<string, string> = {
  INSTITUTION_USER: "Institution user",
  AICTE_REVIEWER: "AICTE reviewer",
  AICTE_APPROVER: "AICTE approver",
  ADMIN: "Administrator",
  AUDITOR: "Auditor",
};

export const roleLabel = (role: string): string => ROLE_LABELS[role] ?? role;

// What the UI offers each role. These only decide what to SHOW; the backend enforces every one of
// them again (backend/app/api/review.py), so changing them here grants nothing.
export const canReview = (role: string): boolean => role === "AICTE_REVIEWER" || role === "AICTE_APPROVER";
export const canDecide = (role: string): boolean => role === "AICTE_APPROVER";
