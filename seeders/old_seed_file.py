import random
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash
from app import app
from models import db, Account, PromissoryRequest, ActiveSettings, ActiveCourse
from faker import Faker
import uuid

fake = Faker()

# Courses
courses = [
    'BACHELOR OF SCIENCE IN ACCOUNTANCY',
    'BACHELOR OF SCIENCE IN MANAGEMENT ACCOUNTING',
    'BACHELOR OF SCIENCE IN NURSING',
    'BACHELOR OF SCIENCE IN HOSPITALITY MANAGEMENT',
    'BACHELOR OF SCIENCE IN CRIMINOLOGY',
    'BACHELOR OF SCIENCE IN INFORMATION TECHNOLOGY',
    'BACHELOR OF SCIENCE IN COMPUTER SCIENCE',
    'BACHELOR OF ARTS IN COMMUNICATION',
    'BACHELOR OF ARTS IN PSYCHOLOGY',
    'BACHELOR OF SCIENCE IN CIVIL ENGINEERING'
]

# Year-level distribution weights
year_levels = ["1st Year", "2nd Year", "3rd Year", "4th Year"]
year_weights = [0.4, 0.3, 0.2, 0.1]

# Promissory reasons
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
semester_types_list = ["Prelims", "Midterms", "Finals"]
school_years_list = ["2024-2025", "2025-2026"]

# Names
first_names = [
    "Juan", "Maria", "Jose", "Ana", "Mark", "John", "Paula", "Miguel", "Sofia", "Daniel",
    "Renz", "Joshua", "Angela", "Carla", "Raven", "Bruce", "Ella", "Nicole", "Liam", "Zyra",
    "Gabriel", "Kimberly", "Nathan", "Isabella", "Ethan", "Angel", "Claire", "Ryan", "Mikaela", "David",
    "Hannah", "Leonardo", "Grace", "Patrick", "Jasmine", "Christian", "Ariana", "Samuel", "Nicoletta", "Rey"
]

last_names = [
    "Santos", "Reyes", "Cruz", "Bautista", "Torres", "Garcia", "Lopez", "Aquino",
    "Martinez", "Flores", "Velasco", "Castillo", "Ramos", "Rivera", "Navarro",
    "DelosSantos", "Villanueva", "Espino", "Salazar", "Pascual", "DelaCruz", "Morales",
    "Cabrera", "Sison", "Alcantara", "Herrera", "Villar", "Padilla", "Soriano", "Lim", "Tan"
]

middle_names = ["A.", "B.", "C.", "D.", "E.", "F.", "G.", "H.", "I.", "J."]
suffixes = ["", "Jr.", "III"]

# Default password pool
passwords_pool = ["pass123", "student2025", "mypassword", "school2025"]

with app.app_context():
    db.drop_all()
    db.create_all()
    print("✔ Database cleared and tables created")

    # Add courses
    for course_name in courses:
        if not ActiveCourse.query.filter_by(name=course_name).first():
            db.session.add(ActiveCourse(name=course_name))
    db.session.commit()
    print(f"✔ {len(courses)} ActiveCourses created")

    # Finance account
    if not Account.query.filter_by(email="finance@school.edu").first():
        finance_account = Account(
            first_name="Finance",
            middle_name="",
            last_name="Admin",
            suffix="",
            email="finance@school.edu",
            _role="Finance",
            _status="Active",
            year_level=None,
            course=None,
            password_hash=generate_password_hash("Finance123!"),
            plain_password="Finance123!"
        )
        db.session.add(finance_account)
        db.session.commit()
        print("✔ Finance account created")

    # Admin accounts
    admins = [
        {"first_name": "Master", "middle_name": "", "last_name": "Admin", "email": "admin@example.com", "password": "Admin@123"},
        {"first_name": "Super", "middle_name": "", "last_name": "Admin", "email": "superadmin@example.com", "password": "SuperAdmin@123"}
    ]

    for admin_data in admins:
        if not Account.query.filter_by(email=admin_data["email"]).first():
            admin = Account(
                first_name=admin_data["first_name"],
                middle_name=admin_data["middle_name"],
                last_name=admin_data["last_name"],
                suffix="",
                email=admin_data["email"],
                _role="Admin",
                _status="Active"
            )
            admin.set_password(admin_data["password"])
            db.session.add(admin)
            db.session.commit()
            print(f"Admin account '{admin_data['email']}' created successfully!")

    # Generate students
    used_names = set()
    used_emails = set()
    students = []

    for course_name in courses:
        total_students = random.randint(80, 120)
        year_counts = [int(total_students * w) for w in year_weights]
        while sum(year_counts) < total_students:
            year_counts[0] += 1

        for idx, year_level in enumerate(year_levels):
            for _ in range(year_counts[idx]):
                # Unique full name
                while True:
                    fn = random.choice(first_names)
                    ln = random.choice(last_names)
                    mn = random.choice(middle_names)
                    suffix = random.choice(suffixes)
                    full_name = f"{fn} {mn} {ln} {suffix}".strip()
                    if full_name not in used_names:
                        used_names.add(full_name)
                        break

                # Unique email
                while True:
                    if random.random() < 0.7:
                        email = f"{fn.lower()}.{ln.lower()}.{uuid.uuid4().hex[:4]}@school.edu"
                    else:
                        email = f"{fn.lower()}.{ln.lower()}{random.randint(1,99)}@school.edu"
                    if email not in used_emails:
                        used_emails.add(email)
                        break

                # Active/inactive
                status = "Active" if random.random() < 0.95 else "Inactive"
                password_default = random.choice(passwords_pool)

                student = Account(
                    first_name=fn,
                    middle_name=mn,
                    last_name=ln,
                    suffix=suffix,
                    email=email,
                    _role="Student",
                    _status=status,
                    year_level=year_level,
                    course=course_name,
                    password_hash=generate_password_hash(password_default),
                    plain_password=password_default
                )
                students.append(student)

    db.session.add_all(students)
    db.session.commit()
    print(f"✔ {len(students)} Student accounts created with unique emails")

    # Generate promissory requests: ensure every student gets at least 1
    students_only = Account.query.filter_by(_role="Student").all()
    promissories = []
    statuses = ["Pending", "Approved", "Rejected"]
    comments_options = [
        "Approved, please settle within 30 days.",
        "Rejected due to missing documents.",
        "Pending review by Finance Office."
    ]

    for school_year in school_years_list:
        for student in students_only:
            num_requests_for_student = 1 + random.randint(0, 2)  # 1–3 requests
            for _ in range(num_requests_for_student):
                request_date = datetime.now(timezone.utc) - timedelta(days=random.randint(1, 180))
                semester_type = "Finals" if request_date.month in [10, 11, 12, 5, 6] else random.choice(semester_types_list)
                status = random.choice(statuses)
                comments = random.choice(comments_options) if status != "Pending" and random.random() < 0.7 else None

                req = PromissoryRequest(
                    student_id=student.id,
                    year_level=student.year_level,
                    course=student.course,
                    email=student.email,
                    reason_text=random.choice(promissory_reasons),
                    status=status,
                    comments=comments,
                    requested_at=request_date,
                    semester_type=semester_type,
                    semester=random.choice(semester_names),
                    school_year=school_year
                )
                promissories.append(req)

    db.session.add_all(promissories)
    db.session.commit()
    print(f"✔ {len(promissories)} Promissory Requests created")

    # Active settings
    active_settings = ActiveSettings(
        active_semester="First Semester",
        active_school_year="2025-2026"
    )
    db.session.add(active_settings)
    db.session.commit()
    print("✔ ActiveSettings created")
    print("🎉 SEEDING COMPLETE!")
