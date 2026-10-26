// Display labels for the backend's role codes (backend/app/models/user.py: Role).
export const ROLE_LABELS: Record<string, string> = {
  INSTITUTION_USER: "Institution user",
  AICTE_REVIEWER: "AICTE reviewer",
  AICTE_APPROVER: "AICTE approver",
  ADMIN: "Administrator",
  AUDITOR: "Auditor",
};

export const roleLabel = (role: string): string => ROLE_LABELS[role] ?? role;
