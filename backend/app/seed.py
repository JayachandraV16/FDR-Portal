"""Synthetic demonstration data. Every record here is fictitious.

Run with:  python -m app.seed
Idempotent: records are matched on their unique `code` and left untouched if present.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session_factory
from app.models import Bank, Institution

SYNTHETIC_BANKS = [
    ("SYN-BANK-01", "Sample National Bank (Synthetic)"),
    ("SYN-BANK-02", "Demo Cooperative Bank (Synthetic)"),
    ("SYN-BANK-03", "Example Regional Bank (Synthetic)"),
]

SYNTHETIC_INSTITUTIONS = [
    ("SYN-INST-001", "Demo Institute of Technology (Synthetic)", "Karnataka", "Engineering"),
    ("SYN-INST-002", "Sample College of Management (Synthetic)", "Maharashtra", "Management"),
    ("SYN-INST-003", "Example Polytechnic (Synthetic)", "Tamil Nadu", "Polytechnic"),
    ("SYN-INST-004", "Placeholder School of Pharmacy (Synthetic)", "Telangana", "Pharmacy"),
]


def seed(session: Session) -> dict[str, int]:
    """Insert missing synthetic rows; return how many of each were created."""
    created = {"banks": 0, "institutions": 0}

    existing_banks = set(session.scalars(select(Bank.code)))
    for code, name in SYNTHETIC_BANKS:
        if code not in existing_banks:
            session.add(Bank(code=code, name=name))
            created["banks"] += 1

    existing_insts = set(session.scalars(select(Institution.code)))
    for code, name, state, inst_type in SYNTHETIC_INSTITUTIONS:
        if code not in existing_insts:
            session.add(
                Institution(
                    code=code,
                    name=name,
                    state=state,
                    institution_type=inst_type,
                    contact_email=f"{code.lower()}@example.invalid",
                )
            )
            created["institutions"] += 1

    session.commit()
    return created


def main() -> None:
    if get_settings().app_env == "production":
        raise SystemExit("Refusing to load synthetic seed data when APP_ENV=production.")
    with get_session_factory()() as session:
        created = seed(session)
    print(f"Seed complete. Created: {created}")


if __name__ == "__main__":
    main()
