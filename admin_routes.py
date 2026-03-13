import io
import secrets
import string

import pandas as pd
from flask import Blueprint, flash, redirect, render_template, request, send_file, session, url_for

from models import db, Account, ActiveSettings, ActiveCourse, SystemLog
from utils.auth import require_role, require_active_user
from utils.logging import log_action


admin_bp = Blueprint(
    "admin",
    __name__,
    url_prefix="/admin",
    template_folder="templates",
)

VALID_ROLES = {"Admin", "Finance", "Student"}
VALID_STATUSES = {"Active", "Inactive"}


# =====================================================
# HELPERS
# =====================================================
def generate_random_password(last_name: str, length: int = 8) -> str:
    """
    Generate a safer temporary password.
    """
    clean_last_name = (last_name or "User").strip() or "User"
    alphabet = string.ascii_letters + string.digits
    random_str = "".join(secrets.choice(alphabet) for _ in range(length))
    return f"{clean_last_name}{random_str}"


def get_full_name(acc) -> str:
    if not acc:
        return "N/A"

    parts = [
        getattr(acc, "first_name", "") or "",
        getattr(acc, "middle_name", "") or "",
        getattr(acc, "last_name", "") or "",
        getattr(acc, "suffix", "") or "",
    ]
    full_name = " ".join(part.strip() for part in parts if part and part.strip()).strip()
    return full_name or getattr(acc, "email", "N/A")


def get_admin_user():
    admin, response = require_active_user(role="Admin")
    return admin, response


def get_active_settings():
    settings = ActiveSettings.query.first()
    if not settings:
        return "Not Set", "Not Set"
    return settings.active_semester or "Not Set", settings.active_school_year or "Not Set"


def get_or_create_active_settings():
    settings = ActiveSettings.query.first()
    if not settings:
        settings = ActiveSettings()
        db.session.add(settings)
        db.session.commit()
    return settings


def normalize_text(value):
    return (value or "").strip()


def normalize_role(value):
    return normalize_text(value).capitalize()


def normalize_status(value):
    return normalize_text(value).capitalize()


def is_valid_role(value):
    return value in VALID_ROLES


def is_valid_status(value):
    return value in VALID_STATUSES


def account_email_exists(email, exclude_account_id=None):
    query = Account.query.filter(Account.email == email)
    if exclude_account_id is not None:
        query = query.filter(Account.id != exclude_account_id)
    return db.session.query(query.exists()).scalar()


def build_account_export_rows(accounts):
    return [
        {
            "ID": account.id,
            "First_Name": account.first_name or "",
            "Middle_Name": account.middle_name or "",
            "Last_Name": account.last_name or "",
            "Suffix": account.suffix or "",
            "Email": account.email or "",
            "Role": getattr(account, "role", "") or getattr(account, "_role", ""),
            "Status": getattr(account, "status", "") or getattr(account, "_status", ""),
            "Year_Level": getattr(account, "year_level", "") or "",
            "Course": getattr(account, "course", "") or "",
        }
        for account in accounts
    ]


def apply_account_filters(query, search="", role_filter="", status_filter=""):
    if search:
        like_value = f"%{search}%"
        query = query.filter(
            (Account.first_name.ilike(like_value))
            | (Account.last_name.ilike(like_value))
            | (Account.email.ilike(like_value))
        )

    if role_filter:
        query = query.filter(Account._role == role_filter)

    if status_filter:
        query = query.filter(Account._status == status_filter)

    return query


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


def get_account_role(account):
    return getattr(account, "role", None) or getattr(account, "_role", None)


def get_account_status(account):
    return getattr(account, "status", None) or getattr(account, "_status", None)


# =====================================================
# DASHBOARD
# =====================================================
@admin_bp.route("/dashboard")
@require_role("Admin")
def dashboard():
    admin, response = get_admin_user()
    if response:
        return response

    total_active = Account.query.filter_by(_status="Active").count()
    total_inactive = Account.query.filter_by(_status="Inactive").count()
    total_active_students = Account.query.filter_by(_status="Active", _role="Student").count()
    total_active_finance = Account.query.filter_by(_status="Active", _role="Finance").count()
    total_active_admin = Account.query.filter_by(_status="Active", _role="Admin").count()

    active_settings = ActiveSettings.query.first()
    active_semester = active_settings.active_semester if active_settings else "Not Set"
    active_school_year = active_settings.active_school_year if active_settings else "Not Set"
    active_course = getattr(active_settings, "active_course", "Not Set (using list model)") if active_settings else "Not Set"

    data = {
        "total_active_accounts": total_active,
        "total_unactivated_accounts": total_inactive,
        "total_active_students": total_active_students,
        "total_active_finance": total_active_finance,
        "total_active_admin": total_active_admin,
        "admin_user": session.get("user_name", "Admin User"),
        "active_semester": active_semester,
        "active_school_year": active_school_year,
        "active_course": active_course,
    }

    log_action(session.get("user_name", "Admin User"), "Viewed dashboard")
    return render_template("admin/dashboard.html", data=data)


# =====================================================
# ACCOUNTS
# =====================================================
@admin_bp.route("/accounts")
@require_role("Admin")
def accounts():
    admin, response = get_admin_user()
    if response:
        return response

    search = normalize_text(request.args.get("search"))
    role_filter = normalize_role(request.args.get("role"))
    status_filter = normalize_status(request.args.get("status"))
    page = request.args.get("page", 1, type=int)
    per_page = 10

    if role_filter and not is_valid_role(role_filter):
        role_filter = ""
    if status_filter and not is_valid_status(status_filter):
        status_filter = ""

    query = apply_account_filters(
        Account.query,
        search=search,
        role_filter=role_filter,
        status_filter=status_filter,
    )

    pagination = query.order_by(Account.id.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )

    active_settings = ActiveSettings.query.first()
    active_semester = active_settings.active_semester if active_settings else "Not Set"
    active_school_year = active_settings.active_school_year if active_settings else "Not Set"
    active_course = getattr(active_settings, "active_course", "Not Set")

    log_action(session.get("user_name", "Admin User"), "Viewed accounts page")

    return render_template(
        "admin/accounts.html",
        accounts=pagination.items,
        pagination=pagination,
        page=page,
        total_pages=pagination.pages,
        admin_user=session.get("user_name", "Admin User"),
        search=search,
        role_filter=role_filter,
        status_filter=status_filter,
        active_semester=active_semester,
        active_school_year=active_school_year,
        active_course=active_course,
    )


# =====================================================
# ADD NEW ACCOUNT
# =====================================================
@admin_bp.route("/add_new_account", methods=["GET", "POST"])
@require_role("Admin")
def add_new_account():
    admin, response = get_admin_user()
    if response:
        return response

    active_courses = ActiveCourse.query.order_by(ActiveCourse.name.asc()).all()

    if request.method == "POST":
        first_name = normalize_text(request.form.get("firstName"))
        middle_name = normalize_text(request.form.get("middleName"))
        last_name = normalize_text(request.form.get("lastName"))
        suffix = normalize_text(request.form.get("suffix"))
        email = normalize_text(request.form.get("email")).lower()
        role = normalize_role(request.form.get("role"))

        if not first_name or not last_name or not email or not role:
            flash("First name, last name, email, and role are required.", "danger")
            return render_template("admin/add_new_account.html", active_courses=active_courses)

        if not is_valid_role(role):
            flash("Invalid role selected.", "danger")
            return render_template("admin/add_new_account.html", active_courses=active_courses)

        if account_email_exists(email):
            flash("Email already exists.", "danger")
            return render_template("admin/add_new_account.html", active_courses=active_courses)

        password = generate_random_password(last_name)

        account = Account(
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            suffix=suffix,
            email=email,
        )

        set_account_role(account, role)
        set_account_status(account, "Active")

        if role == "Student":
            account.year_level = normalize_text(request.form.get("year_level"))
            account.course = normalize_text(request.form.get("course"))
        else:
            account.year_level = None
            account.course = None

        account.set_password(password)

        try:
            db.session.add(account)
            db.session.commit()

            log_action(
                session.get("user_name", "Admin User"),
                f"Added new account: {get_full_name(account)} ({email})",
            )

            return redirect(
                url_for("admin.show_generated_password", pwd=password, email=email)
            )
        except Exception:
            db.session.rollback()
            flash("Failed to create account.", "danger")
            return render_template("admin/add_new_account.html", active_courses=active_courses)

    return render_template("admin/add_new_account.html", active_courses=active_courses)


# =====================================================
# EDIT ACCOUNT
# =====================================================
@admin_bp.route("/edit_account/<int:account_id>", methods=["GET", "POST"])
@require_role("Admin")
def edit_account(account_id):
    admin, response = get_admin_user()
    if response:
        return response

    account = db.session.get(Account, account_id)
    if not account:
        flash("Account not found.", "danger")
        return redirect(url_for("admin.accounts"))

    new_password = None

    if request.method == "POST":
        try:
            if "reset_password" in request.form:
                new_password = generate_random_password(account.last_name)
                account.set_password(new_password)
                db.session.commit()

                flash(f"Password reset successfully. New password: {new_password}", "success")
                log_action(
                    session.get("user_name", "Admin User"),
                    f"Reset password for {get_full_name(account)}",
                )
            else:
                first_name = normalize_text(request.form.get("first_name"))
                middle_name = normalize_text(request.form.get("middle_name"))
                last_name = normalize_text(request.form.get("last_name"))
                suffix = normalize_text(request.form.get("suffix"))
                email = normalize_text(request.form.get("email")).lower()
                role_value = normalize_role(request.form.get("role"))
                status_value = normalize_status(request.form.get("status"))

                if not first_name or not last_name or not email:
                    flash("First name, last name, and email are required.", "danger")
                    return redirect(url_for("admin.edit_account", account_id=account.id))

                if not is_valid_role(role_value):
                    flash("Invalid role selected.", "danger")
                    return redirect(url_for("admin.edit_account", account_id=account.id))

                if not is_valid_status(status_value):
                    flash("Invalid status selected.", "danger")
                    return redirect(url_for("admin.edit_account", account_id=account.id))

                if account_email_exists(email, exclude_account_id=account.id):
                    flash("Email already exists.", "danger")
                    return redirect(url_for("admin.edit_account", account_id=account.id))

                # Basic self-protection: don't let current admin deactivate themselves accidentally
                if account.id == admin.id and status_value != "Active":
                    flash("You cannot deactivate your own admin account.", "danger")
                    return redirect(url_for("admin.edit_account", account_id=account.id))

                if account.id == admin.id and role_value != "Admin":
                    flash("You cannot change your own admin role.", "danger")
                    return redirect(url_for("admin.edit_account", account_id=account.id))

                account.first_name = first_name
                account.middle_name = middle_name
                account.last_name = last_name
                account.suffix = suffix
                account.email = email

                set_account_role(account, role_value)
                set_account_status(account, status_value)

                if role_value == "Student":
                    account.year_level = normalize_text(request.form.get("year_level"))
                    account.course = normalize_text(request.form.get("course"))
                else:
                    account.year_level = None
                    account.course = None

                db.session.commit()

                flash("Account updated successfully", "success")
                log_action(
                    session.get("user_name", "Admin User"),
                    f"Updated account: {get_full_name(account)}",
                )

            return redirect(url_for("admin.edit_account", account_id=account.id))

        except Exception:
            db.session.rollback()
            flash("Failed to update account.", "danger")
            return redirect(url_for("admin.edit_account", account_id=account.id))

    active_courses = ActiveCourse.query.order_by(ActiveCourse.name.asc()).all()
    return render_template(
        "admin/edit_account.html",
        account=account,
        new_password=new_password,
        active_courses=active_courses,
    )


# =====================================================
# SYSTEM LOGS
# =====================================================
@admin_bp.route("/logs")
@require_role("Admin")
def logs():
    admin, response = get_admin_user()
    if response:
        return response

    user_filter = normalize_text(request.args.get("user"))
    action_filter = normalize_text(request.args.get("action"))
    page = request.args.get("page", 1, type=int)
    per_page = 10

    query = SystemLog.query

    if user_filter:
        query = query.filter(SystemLog.user_name.ilike(f"%{user_filter}%"))
    if action_filter:
        query = query.filter(SystemLog.action.ilike(f"%{action_filter}%"))

    logs_pagination = query.order_by(SystemLog.timestamp.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )

    active_semester, active_school_year = get_active_settings()

    return render_template(
        "admin/logs.html",
        logs=logs_pagination,
        active_semester=active_semester,
        active_school_year=active_school_year,
        user_filter=user_filter,
        action_filter=action_filter,
        total_pages=logs_pagination.pages,
    )


# =====================================================
# ACTIVE SEMESTER
# =====================================================
@admin_bp.route("/semester", methods=["GET", "POST"])
@require_role("Admin")
def semester():
    admin, response = get_admin_user()
    if response:
        return response

    active_settings = get_or_create_active_settings()

    if request.method == "POST":
        new_semester = normalize_text(request.form.get("semester"))
        if not new_semester:
            flash("Semester cannot be empty.", "danger")
            return redirect(url_for("admin.semester"))

        old_semester = active_settings.active_semester
        active_settings.active_semester = new_semester

        try:
            db.session.commit()
            flash("Active semester updated successfully!", "success")
            log_action(
                session.get("user_name", "Admin User"),
                f"Changed semester from '{old_semester}' to '{active_settings.active_semester}'",
            )
        except Exception:
            db.session.rollback()
            flash("Failed to update active semester.", "danger")

        return redirect(url_for("admin.semester"))

    return render_template(
        "admin/semester.html",
        active_semester=active_settings.active_semester,
    )


# =====================================================
# ACTIVE SCHOOL YEAR
# =====================================================
@admin_bp.route("/school_year", methods=["GET", "POST"])
@require_role("Admin")
def school_year():
    admin, response = get_admin_user()
    if response:
        return response

    active_settings = get_or_create_active_settings()

    if request.method == "POST":
        new_school_year = normalize_text(request.form.get("school_year"))
        if not new_school_year:
            flash("School year cannot be empty.", "danger")
            return redirect(url_for("admin.school_year"))

        old_year = active_settings.active_school_year
        active_settings.active_school_year = new_school_year

        try:
            db.session.commit()
            flash("Active school year updated successfully!", "success")
            log_action(
                session.get("user_name", "Admin User"),
                f"Changed school year from '{old_year}' to '{active_settings.active_school_year}'",
            )
        except Exception:
            db.session.rollback()
            flash("Failed to update active school year.", "danger")

        return redirect(url_for("admin.school_year"))

    return render_template(
        "admin/school_year.html",
        active_school_year=active_settings.active_school_year,
    )


# =====================================================
# ACTIVE COURSE
# =====================================================
@admin_bp.route("/course", methods=["GET", "POST"])
@require_role("Admin")
def course():
    admin, response = get_admin_user()
    if response:
        return response

    if request.method == "POST":
        course_name = normalize_text(request.form.get("course_name"))
        if not course_name:
            flash("Course name cannot be empty.", "danger")
            return redirect(url_for("admin.course"))

        existing_course = ActiveCourse.query.filter_by(name=course_name).first()
        if existing_course:
            flash(f"Course '{course_name}' is already active.", "warning")
            return redirect(url_for("admin.course"))

        new_course = ActiveCourse(name=course_name)

        try:
            db.session.add(new_course)
            db.session.commit()
            flash(f"Course '{course_name}' added to active list successfully!", "success")
            log_action(
                session.get("user_name", "Admin User"),
                f"Added new course '{course_name}'",
            )
        except Exception:
            db.session.rollback()
            flash("Failed to add course.", "danger")

        return redirect(url_for("admin.course"))

    active_courses = ActiveCourse.query.order_by(ActiveCourse.name.asc()).all()
    return render_template("admin/course.html", active_courses=active_courses)


# =====================================================
# REMOVE COURSE
# =====================================================
@admin_bp.route("/course/delete/<int:course_id>", methods=["POST"])
@require_role("Admin")
def delete_course(course_id):
    admin, response = get_admin_user()
    if response:
        return response

    course = db.session.get(ActiveCourse, course_id)
    if not course:
        flash("Course not found.", "danger")
        return redirect(url_for("admin.course"))

    course_name = course.name

    try:
        db.session.delete(course)
        db.session.commit()
        flash(f"Course '{course_name}' removed from active list.", "info")
        log_action(
            session.get("user_name", "Admin User"),
            f"Deleted course '{course_name}'",
        )
    except Exception:
        db.session.rollback()
        flash("Failed to delete course.", "danger")

    return redirect(url_for("admin.course"))


# =====================================================
# IMPORT ACCOUNTS
# =====================================================
@admin_bp.route("/upload_accounts", methods=["POST"])
@require_role("Admin")
def upload_accounts():
    admin, response = get_admin_user()
    if response:
        return response

    file = request.files.get("file")
    if not file or file.filename == "":
        flash("No file selected.", "warning")
        return redirect(url_for("admin.accounts"))

    filename = file.filename.lower()

    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(file)
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file)
        else:
            flash("Unsupported file type. Please use CSV or Excel.", "danger")
            return redirect(url_for("admin.accounts"))

        df = df.fillna("")

        existing_emails = {
            email.lower()
            for (email,) in db.session.query(Account.email).all()
            if email
        }

        uploaded_rows = []

        for _, row in df.iterrows():
            email = normalize_text(row.get("email")).lower()
            if not email or email in existing_emails:
                continue

            first_name = normalize_text(row.get("first_name"))
            middle_name = normalize_text(row.get("middle_name"))
            last_name = normalize_text(row.get("last_name"))
            suffix = normalize_text(row.get("suffix"))
            role = normalize_role(row.get("role")) or "Student"
            status = normalize_status(row.get("status")) or "Inactive"

            if not first_name or not last_name or not email:
                continue
            if not is_valid_role(role) or not is_valid_status(status):
                continue

            password = generate_random_password(last_name)

            acc = Account(
                first_name=first_name,
                middle_name=middle_name,
                last_name=last_name,
                suffix=suffix,
                email=email,
            )

            set_account_role(acc, role)
            set_account_status(acc, status)

            if role == "Student":
                acc.year_level = normalize_text(row.get("year_level"))
                acc.course = normalize_text(row.get("course"))
            else:
                acc.year_level = None
                acc.course = None

            acc.set_password(password)
            db.session.add(acc)

            existing_emails.add(email)
            uploaded_rows.append(get_full_name(acc))

        db.session.commit()

        if uploaded_rows:
            flash(f"Successfully uploaded {len(uploaded_rows)} accounts.", "success")
            log_action(
                session.get("user_name", "Admin User"),
                f"Uploaded accounts: {', '.join(uploaded_rows)}",
            )
        else:
            flash("No new accounts were added (all emails exist or invalid).", "info")

        return redirect(url_for("admin.accounts"))

    except Exception as exc:
        db.session.rollback()
        flash(f"Upload failed: {str(exc)}", "danger")
        return redirect(url_for("admin.accounts"))


# =====================================================
# DOWNLOAD IMPORT ACCOUNT TEMPLATE
# =====================================================
@admin_bp.route("/download_template")
@require_role("Admin")
def download_template():
    admin, response = get_admin_user()
    if response:
        return response

    headers = [
        "first_name",
        "middle_name",
        "last_name",
        "suffix",
        "email",
        "role",
        "status",
        "year_level",
        "course",
    ]
    df = pd.DataFrame(columns=headers)
    out = io.StringIO()
    df.to_csv(out, index=False)
    out.seek(0)

    log_action(
        session.get("user_name", "Admin User"),
        "Downloaded account upload template",
    )

    return send_file(
        io.BytesIO(out.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="account_upload_template.csv",
    )


# =====================================================
# EXPORT AS CSV
# =====================================================
@admin_bp.route("/export_csv")
@require_role("Admin")
def export_csv():
    admin, response = get_admin_user()
    if response:
        return response

    accounts = Account.query.order_by(Account.id.asc()).all()
    data = build_account_export_rows(accounts)

    df = pd.DataFrame(data)
    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)

    log_action(session.get("user_name", "Admin User"), "Exported all accounts to CSV")

    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="accounts.csv",
    )


# =====================================================
# EXPORT AS EXCEL
# =====================================================
@admin_bp.route("/export_excel")
@require_role("Admin")
def export_excel():
    admin, response = get_admin_user()
    if response:
        return response

    accounts = Account.query.order_by(Account.id.asc()).all()
    data = build_account_export_rows(accounts)

    df = pd.DataFrame(data)
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Accounts")

    output.seek(0)

    log_action(session.get("user_name", "Admin User"), "Exported all accounts to Excel")

    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="accounts.xlsx",
    )


# =====================================================
# GENERATED PASSWORD VIEW
# =====================================================
@admin_bp.route("/generated-password")
@require_role("Admin")
def show_generated_password():
    admin, response = get_admin_user()
    if response:
        return response

    password = request.args.get("pwd", "")
    email = request.args.get("email", "")
    return render_template(
        "admin/generated_password.html",
        password=password,
        email=email,
        admin_user=session.get("user_name", "Admin User"),
    )


# =====================================================
# LOGOUT
# =====================================================
@admin_bp.route("/logout", methods=["POST"])
@require_role("Admin")
def logout():
    user_name = session.get("user_name", "Admin User")
    session.clear()
    flash("You have been logged out.", "danger")
    log_action(user_name, "Logged out")
    return redirect(url_for("login"))