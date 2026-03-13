import random
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash
from app import app
from models import db, Account, PromissoryRequest, ActiveSettings, ActiveCourse
from faker import Faker
import uuid

fake = Faker()

courses = [
    "Bachelor of Science in Accountancy",
    "Bachelor of Science in Management Accounting",
    "Bachelor of Science in Nursing",
    "Bachelor of Science in Hospitality Management",
    "Bachelor of Science in Criminology",
    "Bachelor of Science in Information Technology",
    "Bachelor of Science in Computer Science",
    "Bachelor or Arts in Communication",
    "Bachelor or Arts in Psychology",
    "Bachelor of Science in Civil Engineering"
]

year_levels = ["1st Year", "2nd Year", "3rd Year", "4th Year"]
year_weights = [0.38, 0.28, 0.22, 0.12]

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

password_default = "password123"

# Expanded first and last names pools
first_names = [
    "Juan", "Maria", "Jose", "Ana", "Mark", "John", "Paula", "Miguel", "Sofia", "Daniel",
    "Renz", "Joshua", "Angela", "Carla", "Raven", "Bruce", "Ella", "Nicole", "Liam", "Zyra",
    "Gabriel", "Kimberly", "Nathan", "Isabella", "Ethan", "Angel", "Claire", "Ryan", "Mikaela", "David",
    "Hannah", "Leonardo", "Grace", "Patrick", "Jasmine", "Christian", "Ariana", "Samuel", "Nicoletta", "Rey",
    "Oliver", "Emma", "Lucas", "Chloe", "Sophia", "Benjamin", "Victoria", "Elijah", "Maya", "Leo",
    "Carlo", "Ellaine", "Rafael", "Katrina", "Dominic", "Angelica", "Jonathan", "Danica", "Kevin", "Abigail",
    "Anthony", "Nicole", "Michael", "Patricia", "Joshua", "Camille", "Adrian", "Francesca", "Jacob", "Gabrielle",
    "Sean", "Sophia", "Vincent", "Alyssa", "Christian", "Beatrice", "Aaron", "Charlotte", "Markus", "Clarisse",
    "Julian", "Vanessa", "Edwin", "Marianne", "Leo", "Diana", "Harrison", "Paula", "Ian", "Rachel"
]

last_names = [
    "Santos", "Reyes", "Cruz", "Bautista", "Torres", "Garcia", "Lopez", "Aquino",
    "Martinez", "Flores", "Velasco", "Castillo", "Ramos", "Rivera", "Navarro",
    "DelosSantos", "Villanueva", "Espino", "Salazar", "Pascual", "DelaCruz", "Morales",
    "Cabrera", "Sison", "Alcantara", "Herrera", "Villar", "Padilla", "Soriano", "Lim", "Tan",
    "Lozada", "Magno", "Ortega", "De Guzman", "Mendoza", "Pineda", "Fabian", "Santiago", "Cordero",
    "Carreon", "Tupas", "Valdez", "Vergara", "Manalo", "Bayani", "Abella", "Castañeda", "Rosales", "Salvador",
    "DelaRosa", "Marquez", "Lagman", "Delgado", "Antonio", "Gonzales", "Buenaventura", "Ferrer", "Torralba", "Alvarez",
    "Cordero", "Labrador", "Padua", "Dimaano", "Malvar", "Roces", "Aguilar", "Castro", "Roldan", "Serrano",
    "Balagtas", "Alfaro", "Lazaro", "Bacani", "Villanueva", "Soriano", "Delgado", "Navarro", "Ramos", "Tañada"
]

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
            password_hash=generate_password_hash(password_default),
            plain_password=password_default
        )
        db.session.add(finance_account)
        db.session.commit()
        print("✔ Finance account created")

    # Admin accounts
    admins = [
        {
            "first_name": "Master",
            "middle_name": "",
            "last_name": "Admin",
            "email": "admin@example.com",
            "password": "Admin@123"
        },
        {
            "first_name": "Super",
            "middle_name": "",
            "last_name": "Admin",
            "email": "superadmin@example.com",
            "password": "SuperAdmin@123"
        }
    ]

    for admin_data in admins:
        existing_admin = Account.query.filter_by(email=admin_data["email"]).first()
        if existing_admin:
            print(f"Admin account '{admin_data['email']}' already exists.")
        else:
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

    # Generate students with unique names
    used_names = set()
    students = []
    for course_name in courses:
        total_students = random.randint(80, 120)
        year_counts = [int(total_students * w) for w in year_weights]
        while sum(year_counts) < total_students:
            year_counts[0] += 1

        for idx, year_level in enumerate(year_levels):
            for _ in range(year_counts[idx]):
                while True:
                    fn = random.choice(first_names)
                    ln = random.choice(last_names)
                    full_name = f"{fn} {ln}"
                    if full_name not in used_names:
                        used_names.add(full_name)
                        break

                email = f"{fn.lower()}.{ln.lower()}.{uuid.uuid4().hex[:6]}@school.edu"
                student = Account(
                    first_name=fn,
                    middle_name="",
                    last_name=ln,
                    suffix="",
                    email=email,
                    _role="Student",
                    _status="Active",
                    year_level=year_level,
                    course=course_name,
                    password_hash=generate_password_hash(password_default),
                    plain_password=password_default
                )
                students.append(student)

    db.session.add_all(students)
    db.session.commit()
    print(f"✔ {len(students)} Student accounts created with unique names")

    # Active settings
    active_settings = ActiveSettings(
        active_semester="First Semester",
        active_school_year="2025-2026"
    )
    db.session.add(active_settings)
    db.session.commit()
    print("✔ ActiveSettings created")
    print("🎉 SEEDING COMPLETE!")
