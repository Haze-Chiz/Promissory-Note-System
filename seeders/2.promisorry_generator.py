# insert_promissory_requests_multi.py
import pandas as pd
import random
from datetime import datetime, timedelta
from sqlalchemy import and_
from app import app
from models import db, Account, PromissoryRequest

# -------------------------
# CONFIG
# -------------------------
input_file = r"C:\Users\Haze\Downloads\accounts.xlsx"
min_notes = 1      # minimum promissory per student
max_notes = 5      # maximum promissory per student

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
semester_types = ["Prelims", "Midterms", "Finals"]
school_years = ["2024-2025", "2025-2026"]
statuses = ["Pending", "Approved", "Rejected"]

# -------------------------
# LOAD EXCEL
# -------------------------
df = pd.read_excel(input_file)
students_df = df[df["Role"].astype(str).str.lower() == "student"]

if students_df.empty:
    print("No students found in Excel!")
    raise SystemExit(1)

# -------------------------
# DB INSERT
# -------------------------
with app.app_context():

    created = 0
    skipped = 0

    for _, row in students_df.iterrows():

        email = str(row.get("Email", "")).strip()
        first = str(row.get("First_Name", "")).strip()
        last = str(row.get("Last_Name", "")).strip()

        # LOOKUP existing account only
        account = None

        if email:
            account = Account.query.filter_by(email=email).first()

        if account is None and first and last:
            account = Account.query.filter(
                and_(
                    Account.first_name == first,
                    Account.last_name == last,
                    Account._role == "Student"
                )
            ).first()

        if account is None:
            print(f"Skipping (no account): {first} {last} / {email}")
            skipped += 1
            continue

        # Generate 1–5 promissory notes
        note_count = random.randint(min_notes, max_notes)

        for _ in range(note_count):

            requested_at = datetime.now() - timedelta(days=random.randint(1, 160))
            requested_at = requested_at.replace(microsecond=0)

            pr = PromissoryRequest(
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

            db.session.add(pr)
            created += 1

    db.session.commit()

print("✔ DONE INSERTING PROMISSORY NOTES")
print(f"Total created: {created}")
print(f"Skipped (no account): {skipped}")
