from app import create_app
from models import db, Account


SUPERADMIN_ACCOUNT = {
    "first_name": "Super",
    "middle_name": None,
    "last_name": "Admin",
    "suffix": None,
    "email": "superadmin@fcpc.edu.ph",
    "role": "Superadmin",
    "status": "Active",
    "password": "Superadmin123!",
}

ADMIN_ACCOUNT = {
    "first_name": "System",
    "middle_name": None,
    "last_name": "Admin",
    "suffix": None,
    "email": "admin@fcpc.edu.ph",
    "role": "Admin",
    "status": "Active",
    "password": "Admin123!",
}


def set_account_role(account, role_value):
    if hasattr(account, "role"):
        account.role = role_value
    else:
        setattr(account, "_role", role_value)


def set_account_status(account, status_value):
    if hasattr(account, "status"):
        account.status = status_value
    else:
        setattr(account, "_status", status_value)


def create_account_if_missing(account_data):
    existing_account = Account.query.filter_by(email=account_data["email"]).first()
    if existing_account:
        print(f"[SKIPPED] Account already exists: {account_data['email']}")
        return False

    account = Account(
        first_name=account_data["first_name"],
        middle_name=account_data["middle_name"],
        last_name=account_data["last_name"],
        suffix=account_data["suffix"],
        email=account_data["email"],
    )

    set_account_role(account, account_data["role"])
    set_account_status(account, account_data["status"])

    if hasattr(account, "year_level"):
        account.year_level = None
    if hasattr(account, "course"):
        account.course = None

    account.set_password(account_data["password"])

    db.session.add(account)
    print(
        f"[CREATED] {account_data['role']}: {account_data['email']} | "
        f"Password: {account_data['password']}"
    )
    return True


def seed_admin_and_superadmin():
    created_any = False

    created_any |= create_account_if_missing(SUPERADMIN_ACCOUNT)
    created_any |= create_account_if_missing(ADMIN_ACCOUNT)

    if created_any:
        db.session.commit()
        print("\nSeeding completed successfully.")
    else:
        print("\nNo new accounts were created.")

    print("\nDefault Accounts:")
    print(f"Superadmin -> {SUPERADMIN_ACCOUNT['email']} / {SUPERADMIN_ACCOUNT['password']}")
    print(f"Admin      -> {ADMIN_ACCOUNT['email']} / {ADMIN_ACCOUNT['password']}")


if __name__ == "__main__":
    app = create_app()

    with app.app_context():
        try:
            seed_admin_and_superadmin()
        except Exception as exc:
            db.session.rollback()
            print(f"\n[ERROR] Failed to seed accounts: {exc}")