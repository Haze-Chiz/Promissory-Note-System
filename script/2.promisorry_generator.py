# insert_promissory_requests_multi.py
import os
import random
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import and_

from app import app
from models import db, Account, PromissoryRequest

# -------------------------
# CONFIG
# -------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
input_file = os.path.join(BASE_DIR, "accounts.xlsx")

min_notes = 1      # minimum promissory per student
max_notes = 3      # maximum promissory per student

promissory_reasons = [
    "Financial hardship due to family emergency.",
    "Delay in allowance from sponsor.",
    "Unexpected medical expenses.",
    "Temporary loss of income in the family.",
    "Parents are currently unemployed.",
    "Savings are insufficient this month.",
    "Awaiting scholarship disbursement.",
    "Unexpected household expenses.",
    "Need extension to settle tuition fees.",
    "Still processing financial documents."
]

semester_names = ["First Semester", "Second Semester", "Mid Year"]
semester_types = ["Prelim", "Midterm", "Final"]
school_years = ["2023-2024", "2024-2025", "2025-2026"]
statuses = ["Pending", "Approved", "Rejected"]

REQUIRED_EXCEL_COLUMNS = ["Role", "Email", "First_Name", "Last_Name"]


# -------------------------
# HELPERS
# -------------------------
def normalize_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def validate_config():
    if min_notes < 1 or max_notes < 1:
        raise ValueError("min_notes and max_notes must be at least 1.")
    if min_notes > max_notes:
        raise ValueError("min_notes cannot be greater than max_notes.")


def validate_excel_file(path):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Excel file not found: {path}\n"
            f"Place 'accounts.xlsx' in the same folder as this script:\n{BASE_DIR}"
        )


def load_students_from_excel(path):
    df = pd.read_excel(path)

    missing_columns = [col for col in REQUIRED_EXCEL_COLUMNS if col not in df.columns]
    if missing_columns:
        raise ValueError(
            f"Missing required Excel column(s): {', '.join(missing_columns)}"
        )

    students_df = df[df["Role"].astype(str).str.strip().str.lower() == "student"].copy()

    if students_df.empty:
        raise ValueError("No students found in Excel.")

    return students_df


def find_existing_student_account(email, first_name, last_name):
    account = None

    if email:
        account = Account.query.filter_by(email=email).first()
        if account and str(getattr(account, "_role", "")).strip().lower() != "student":
            account = None

    if account is None and first_name and last_name:
        account = Account.query.filter(
            and_(
                Account.first_name == first_name,
                Account.last_name == last_name,
                Account._role == "Student"
            )
        ).first()

    return account


def has_required_account_fields(account):
    missing = []

    if not getattr(account, "year_level", None):
        missing.append("year_level")
    if not getattr(account, "course", None):
        missing.append("course")
    if not getattr(account, "email", None):
        missing.append("email")

    return missing


# -------------------------
# MAIN
# -------------------------
def main():
    validate_config()
    validate_excel_file(input_file)

    print(f"Reading Excel file: {input_file}")
    students_df = load_students_from_excel(input_file)

    created = 0
    skipped_no_account = 0
    skipped_missing_fields = 0
    row_errors = 0

    with app.app_context():
        try:
            for index, row in students_df.iterrows():
                email = normalize_text(row.get("Email"))
                first = normalize_text(row.get("First_Name"))
                last = normalize_text(row.get("Last_Name"))

                try:
                    account = find_existing_student_account(email, first, last)

                    if account is None:
                        print(f"[SKIP] No matching account: {first} {last} / {email}")
                        skipped_no_account += 1
                        continue

                    missing_fields = has_required_account_fields(account)
                    if missing_fields:
                        print(
                            f"[SKIP] Account #{account.id} missing required field(s): "
                            f"{', '.join(missing_fields)} | {account.first_name} {account.last_name}"
                        )
                        skipped_missing_fields += 1
                        continue

                    note_count = random.randint(min_notes, max_notes)

                    for _ in range(note_count):
                        requested_at = datetime.now() - timedelta(days=random.randint(1, 160))
                        requested_at = requested_at.replace(microsecond=0)

                        promissory = PromissoryRequest(
                            student_id=account.id,
                            year_level=account.year_level,
                            course=account.course,
                            email=account.email,
                            reason_text=random.choice(promissory_reasons),
                            status=random.choice(statuses),
                            comments=None,
                            requested_at=requested_at,
                            semester=random.choice(semester_names),
                            semester_type=random.choice(semester_types),
                            school_year=random.choice(school_years)
                        )

                        db.session.add(promissory)
                        created += 1

                except Exception as row_exc:
                    db.session.rollback()
                    row_errors += 1
                    print(
                        f"[ERROR] Failed on Excel row {index + 2} "
                        f"({first} {last} / {email}): {row_exc}"
                    )

            db.session.commit()

        except Exception as exc:
            db.session.rollback()
            raise RuntimeError(f"Database insert failed: {exc}") from exc

    print("\n✔ DONE INSERTING PROMISSORY NOTES")
    print(f"Total created: {created}")
    print(f"Skipped (no account): {skipped_no_account}")
    print(f"Skipped (missing account fields): {skipped_missing_fields}")
    print(f"Row errors: {row_errors}")


if __name__ == "__main__":
    main()