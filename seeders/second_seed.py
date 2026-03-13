from app import app
from models import db, Account

with app.app_context():
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
