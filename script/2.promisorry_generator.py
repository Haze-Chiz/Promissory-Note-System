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

min_notes = 3      # minimum promissory per student
max_notes = 10      # maximum promissory per student

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
    "Still processing financial documents.",

    "Delayed salary of parent or guardian.",
    "Family business experiencing low income.",
    "Recent hospitalization of a family member.",
    "Funds allocated for tuition were used for emergency.",
    "Ongoing financial obligations at home.",
    "Recent natural disaster affected family income.",
    "Unexpected travel expenses due to family matters.",
    "Tuition funds were delayed due to bank processing.",
    "Additional school-related expenses exceeded budget.",
    "Guardian is currently recovering from illness.",

    "Partial payment already made, remaining balance pending.",
    "Late remittance from overseas family member.",
    "Pending release of financial aid.",
    "Unexpected increase in living expenses.",
    "Funds reallocated for urgent home repairs.",
    "Family prioritizing medical needs over tuition.",
    "Delayed income from small business.",
    "Awaiting loan approval.",
    "Short-term financial instability.",
    "Multiple dependents supported by family income.",

    "Recent job loss of parent.",
    "Income affected by seasonal employment.",
    "Family prioritizing basic necessities.",
    "Unexpected expenses due to school projects.",
    "Financial support reduced temporarily.",
    "Awaiting payout from insurance claim.",
    "Unexpected fees from other obligations.",
    "Delayed pension release.",
    "Family facing financial restructuring.",
    "Recent relocation expenses.",

    "Temporary delay in receiving allowance.",
    "Budget constraints due to inflation.",
    "Unexpected cost of transportation.",
    "Pending reimbursement from employer.",
    "Family savings depleted due to emergency.",
    "Multiple tuition payments due simultaneously.",
    "Unexpected academic-related expenses.",
    "Household income affected by recent events.",
    "Delayed financial support from relatives.",
    "Unexpected bills accumulated this month.",

    "Awaiting release of government assistance.",
    "Family prioritizing rent and utilities.",
    "Recent accident caused financial strain.",
    "Income reduced due to reduced working hours.",
    "Family recovering from financial setback.",
    "Unexpected legal expenses.",
    "Pending business income collection.",
    "Emergency repairs at home required funds.",
    "Additional expenses for sibling’s education.",
    "Financial obligations increased unexpectedly.",

    "Delay in scholarship validation process.",
    "Unexpected expenses for health maintenance.",
    "Family affected by economic downturn.",
    "Income diverted to urgent family needs.",
    "Awaiting salary adjustment release.",
    "Unexpected academic fees incurred.",
    "Medical maintenance costs increased.",
    "Family income delayed due to employer issues.",
    "Shortfall in monthly budget allocation.",
    "Temporary financial mismanagement.",

    "Unexpected travel due to family emergency.",
    "Delayed freelance income payment.",
    "Family dealing with debt obligations.",
    "Unexpected increase in tuition-related costs.",
    "Awaiting payment from client or employer.",
    "Temporary pause in income stream.",
    "Financial support redirected to urgent needs.",
    "Unexpected maintenance costs at home.",
    "Budget shortage due to inflation increase.",
    "Recent unexpected expenditures.",

    "Delayed cash flow from family business.",
    "Pending release of academic subsidy.",
    "Additional medical tests required funding.",
    "Emergency support needed for relatives.",
    "Unexpected utility bill increase.",
    "Income affected by external circumstances.",
    "Awaiting funds from external sponsor.",
    "Recent family financial adjustments.",
    "Budget constraints due to multiple expenses.",
    "Short-term inability to pay full tuition.",

    "Unexpected expenses related to internship.",
    "Delayed stipend release.",
    "Additional costs due to academic requirements.",
    "Temporary shortage of funds.",
    "Awaiting financial support confirmation.",
    "Unexpected increase in cost of living.",
    "Family focusing on urgent priorities.",
    "Pending approval of financial assistance.",
    "Recent unexpected financial burden.",
    "Temporary financial constraints due to emergency.",

    "Awaiting remittance from abroad.",
    "Unexpected expenses due to pandemic recovery.",
    "Family income disrupted by recent events.",
    "Short-term delay in tuition payment capability.",
    "Financial adjustments due to household needs.",
    "Unexpected expenses related to transportation.",
    "Budget allocation shifted to urgent needs.",
    "Awaiting confirmation of payment source.",
    "Temporary financial difficulty this semester.",
    "Family experiencing financial transition period."
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


def validate_config(min_notes: int, max_notes: int) -> None:
    if min_notes < 1 or max_notes < 1:
        raise ValueError("Values must be positive.")

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
    validate_config(min_notes, max_notes)
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