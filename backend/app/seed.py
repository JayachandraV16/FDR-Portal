"""Demo reference data: publicly known Indian banks and Maharashtra higher-education institutions.

Only names (and, for institutions, state and a broad type) are used. The `code` values are
internal lookup keys for this portal; they are NOT official AICTE institution IDs, bank IFSC
prefixes or any other regulatory identifier. No account numbers, branches or financial records are
seeded, and nothing here implies that any institution holds, or has had verified, an FDR.
Contact e-mail addresses use the reserved `.invalid` domain.

Run with:  python -m app.seed
Idempotent: records are matched on their unique `code` and left untouched if present, so rows
seeded earlier (for example the SYN-* placeholders) are never modified or removed.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session_factory
from app.models import Bank, Institution

DEMO_BANKS = [
    ("BOM", "Bank of Maharashtra"),
    ("SBI", "State Bank of India"),
    ("ICICI", "ICICI Bank"),
    ("BOB", "Bank of Baroda"),
    ("HDFC", "HDFC Bank"),
    ("CANARA", "Canara Bank"),
]

# (code, name, state, institution_type)
DEMO_INSTITUTIONS = [
    ("PICT", "Pune Institute of Computer Technology", "Maharashtra", "Engineering"),
    ("COEP", "COEP Technological University", "Maharashtra", "Technological University"),
    ("IITB", "Indian Institute of Technology Bombay", "Maharashtra", "Institute of National Importance"),
    ("VJTI", "Veermata Jijabai Technological Institute", "Maharashtra", "Autonomous Engineering Institute"),
    ("WCE", "Walchand College of Engineering, Sangli", "Maharashtra", "Autonomous Engineering Institute"),
    ("ICT", "Institute of Chemical Technology, Mumbai", "Maharashtra", "Deemed University"),
]


def seed(session: Session) -> dict[str, int]:
    """Insert missing demo rows; return how many of each were created."""
    created = {"banks": 0, "institutions": 0}

    existing_banks = set(session.scalars(select(Bank.code)))
    for code, name in DEMO_BANKS:
        if code not in existing_banks:
            session.add(Bank(code=code, name=name))
            created["banks"] += 1

    existing_insts = set(session.scalars(select(Institution.code)))
    for code, name, state, inst_type in DEMO_INSTITUTIONS:
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
        raise SystemExit("Refusing to load demo seed data when APP_ENV=production.")
    with get_session_factory()() as session:
        created = seed(session)
    print(f"Seed complete. Created: {created}")


if __name__ == "__main__":
    main()
