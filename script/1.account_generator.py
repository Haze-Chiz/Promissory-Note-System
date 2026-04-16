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
    "Bachelor of Arts in Communication",
    "Bachelor of Arts in Psychology",
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
    "Still processing financial documents.",

    "Delay in salary of parent or guardian.",
    "Recent family hospitalization caused financial strain.",
    "Tuition payment will be settled after next salary release.",
    "Family currently prioritizing urgent household expenses.",
    "Waiting for remittance from relatives abroad.",
    "Delay in release of educational loan funds.",
    "Pending approval of financial assistance application.",
    "School fees will be paid after scholarship confirmation.",
    "Parent's business experienced temporary financial loss.",
    "Unexpected repair expenses at home affected finances.",
    "Funds are temporarily unavailable due to emergency spending.",
    "Awaiting release of government educational assistance.",
    "Parent's salary deduction affected available funds this month.",
    "Family encountered sudden transportation or relocation expenses.",
    "Recent calamity or disaster affected family finances.",
    "Delayed bank transaction or financial processing.",
    "Tuition payment will be settled once pending funds are received.",
    "Temporary financial difficulty due to family obligations.",
    "Unexpected increase in household expenses this month.",
    "Financial support from sponsor is still being processed."
]

semester_names = ["First Semester", "Second Semester", "Mid Year"]
semester_types_list = ["Prelim", "Midterm", "Final"]
school_years_list = ["2023-2024", "2024-2025", "2025-2026"]

password_default = "password123"

first_names = [
    "Juan", "Maria", "Jose", "Ana", "Mark", "John", "Paula", "Miguel", "Sofia", "Daniel",
    "Renz", "Joshua", "Angela", "Carla", "Raven", "Bruce", "Ella", "Nicole", "Liam", "Zyra",
    "Gabriel", "Kimberly", "Nathan", "Isabella", "Ethan", "Angel", "Claire", "Ryan", "Mikaela", "David",
    "Hannah", "Leonardo", "Grace", "Patrick", "Jasmine", "Christian", "Ariana", "Samuel", "Nicoletta", "Rey",
    "Oliver", "Emma", "Lucas", "Chloe", "Sophia", "Benjamin", "Victoria", "Elijah", "Maya", "Leo",
    "Carlo", "Ellaine", "Rafael", "Katrina", "Dominic", "Angelica", "Jonathan", "Danica", "Kevin", "Abigail",
    "Anthony", "Nicole", "Michael", "Patricia", "Joshua", "Camille", "Adrian", "Francesca", "Jacob", "Gabrielle",
    "Sean", "Sophia", "Vincent", "Alyssa", "Christian", "Beatrice", "Aaron", "Charlotte", "Markus", "Clarisse",
    "Julian", "Vanessa", "Edwin", "Marianne", "Leo", "Diana", "Harrison", "Paula", "Ian", "Rachel",
    "Alvin", "Bryan", "Cedric", "Derrick", "Enzo", "Franco", "Gian", "Harvey", "Ivan", "Jared",
    "Kurt", "Lester", "Marvin", "Neil", "Oscar", "Paolo", "Quentin", "Ralph", "Steve", "Tristan",
    "Ulysses", "Victor", "Warren", "Xander", "Yves", "Zachary",
    "Aileen", "Bianca", "Cassandra", "Denise", "Elaine", "Faith", "Giselle", "Hazel", "Irene", "Joy",
    "Karen", "Liza", "Megan", "Nina", "Olivia", "Princess", "Queenie", "Rica", "Sheila", "Therese",
    "Ursula", "Vera", "Wendy", "Xenia", "Yasmin", "Zara",
    "Andrei", "Benedict", "Carl", "Diego", "Erwin", "Felix", "Gerald", "Henry", "Isaac", "Jayson",
    "Kris", "Louie", "Marlon", "Noel", "Owen", "Philip", "Rico", "Seth", "Timothy", "Ulrich",
    "Vince", "Wilfred", "Xyron", "Yuri", "Zion",
    "Aubrey", "Brianna", "Celine", "Daphne", "Erika", "Fiona", "Glenda", "Heidi", "Isabel", "Janine",
    "Kristine", "Leah", "Monica", "Nadine", "Odessa", "Pamela", "Queena", "Ruth", "Samantha", "Tiffany",
    "Una", "Valerie", "Whitney", "Xyza", "Yvette", "Zenaida",
    "Arvin", "Brent", "Clifford", "Dennis", "Emmanuel", "Francis", "Gilbert", "Hector", "Irvin", "Jerome",
    "Karl", "Lawrence", "Melvin", "Norbert", "Orlando", "Percy", "Ruben", "Stanley", "Terrence", "Virgil",
    "Althea", "Bea", "Chantal", "Darlene", "Eunice", "Florence", "Gretchen", "Helena", "Ivana", "Jessa",
    "Kaye", "Lourdes", "Maricel", "Noemi", "Ofelia", "Pauline", "Rochelle", "Shaina", "Tricia", "Vienna"
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
    "Balagtas", "Alfaro", "Lazaro", "Bacani", "Villanueva", "Soriano", "Delgado", "Navarro", "Ramos", "Tañada",
    "Abad", "Abalos", "Abrenica", "Adriano", "Agbayani", "Agcaoili", "Agustin", "Alano", "Alba", "Alcaraz",
    "Alipio", "Altamirano", "Amador", "Amante", "Ampatuan", "Andrada", "Angeles", "Ansay", "Apostol",
    "Arce", "Arevalo", "Arroyo", "Atienza", "Austria",
    "Baldonado", "Ballesteros", "Banal", "Bangayan", "Bantug", "Barrios", "Basco", "Bautista", "Belmonte",
    "Benitez", "Bernabe", "Bernardino", "Borja", "Briones", "Bulatao",
    "Cabral", "Calderon", "Callanta", "Camacho", "Campos", "Canlas", "Capili", "Carandang", "Cardona",
    "Cariaga", "Casimiro", "Celis", "Chavez", "Ching", "Cojuangco", "Colmenares", "Concepcion",
    "Dagdag", "Damaso", "Datu", "David", "De Jesus", "De Leon", "De Mesa", "De Vera", "De Villa",
    "Decena", "Del Mundo", "Dela Fuente", "Dela Torre", "Dela Vega", "Delos Reyes", "Dizon", "Domingo",
    "Echavez", "Ejercito", "Elizalde", "Encarnacion", "Endaya", "Enriquez", "Escobar", "Espiritu",
    "Fajardo", "Falcon", "Felipe", "Fernandez", "Fernando", "Fortun", "Fuentes",
    "Galang", "Galvez", "Garces", "Gatchalian", "Gomez", "Gorospe", "Guanzon", "Guerrero", "Guinto",
    "Hernandez", "Hilario", "Hipolito", "Hizon",
    "Ilagan", "Inocencio", "Isidro",
    "Jalandoni", "Javier", "Jocson", "Juliano",
    "Lacson", "Ladlad", "Lao", "Lariosa", "Legaspi", "Lorenzo", "Lucero",
    "Macapagal", "Magtanggol", "Malabanan", "Maliksi", "Mangahas", "Manzano", "Mariano",
    "Matias", "Medina", "Mercado", "Miranda", "Montero", "Montoya",
    "Natividad", "Nepomuceno", "Nolasco",
    "Ocampo", "Ong", "Ordonez", "Oreta",
    "Pablo", "Pacquiao", "Pajarillo", "Palma", "Panganiban", "Pangilinan", "Paredes",
    "Parungao", "Pelayo", "Peralta", "Ponce", "Puno",
    "Quimbo", "Quintos",
    "Recto", "Regalado", "Resurreccion", "Ricafort", "Rigor", "Robles", "Romualdez", "Roxas",
    "Samson", "San Pedro", "San Juan", "Sandoval", "Sarmiento", "Sebastian", "Soliman", "Suarez",
    "Tabora", "Tagle", "Talavera", "Tamayo", "Tanchanco", "Tapia", "Tejada", "Tenorio",
    "Tolentino", "Trinidad", "Tuazon",
    "Umali", "Urbano",
    "Valencia", "Vargas", "Vasquez", "Ventura", "Verano", "Vidal",
    "Yabut", "Yap", "Ylagan",
    "Zamora", "Zaragoza", "Zialcita"
]

with app.app_context():
    db.drop_all()
    db.create_all()
    print("✔ Database cleared and tables created")

    for course_name in courses:
        if not ActiveCourse.query.filter_by(name=course_name).first():
            db.session.add(ActiveCourse(name=course_name))
    db.session.commit()
    print(f"✔ {len(courses)} ActiveCourses created")

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

    used_names = set()
    students = []
    for course_name in courses:
        total_students = random.randint(200, 300)
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

    active_settings = ActiveSettings(
        active_semester="First Semester",
        active_school_year="2025-2026"
    )
    db.session.add(active_settings)
    db.session.commit()
    print("✔ ActiveSettings created")
    print("🎉 SEEDING COMPLETE!")
