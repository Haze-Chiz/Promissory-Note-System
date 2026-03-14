import io
import re
import secrets
import string

import pandas as pd
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

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
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# =====================================================
# HELPERS
# =====================================================
def get_admin_display_name():
    return session.get("user_name", "Admin User")


def generate_random_password(last_name: str, length: int = 8) -> str:
    """
    Generate a temporary password using the user's last name plus
    a cryptographically secure random suffix.
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


def get_active_settings_record():
    return ActiveSettings.query.first()


def get_or_create_active_settings():
    settings = ActiveSettings.query.first()
    if not settings:
        settings = ActiveSettings()
        db.session.add(settings)
        db.session.commit()
    return settings


def normalize_text(value):
    return (value or "").strip()


def normalize_email(value):
    return normalize_text(value).lower()


def normalize_role(value):
    return normalize_text(value).capitalize()


def normalize_status(value):
    return normalize_text(value).capitalize()


def normalize_optional_student_field(value):
    cleaned = normalize_text(value)
    return cleaned or None


def is_valid_role(value):
    return value in VALID_ROLES


def is_valid_status(value):
    return value in VALID_STATUSES


def is_valid_email(value):
    return bool(value and EMAIL_PATTERN.match(value))


def account_email_exists(email, exclude_account_id=None):
    query = Account.query.filter(Account.email == email)
    if exclude_account_id is not None:
        query = query.filter(Account.id != exclude_account_id)
    return db.session.query(query.exists()).scalar()


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


def build_account_export_rows(accounts):
    return [
        {
            "ID": account.id,
            "First_Name": account.first_name or "",
            "Middle_Name": account.middle_name or "",
            "Last_Name": account.last_name or "",
            "Suffix": account.suffix or "",
            "Email": account.email or "",
            "Role": get_account_role(account) or "",
            "Status": get_account_status(account) or "",
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


def validate_account_payload(
    *,
    first_name,
    last_name,
    email,
    role,
    status=None,
    course=None,
    exclude_account_id=None,
):
    if not first_name or not last_name or not email:
        return "First name, last name, and email are required."

    if not is_valid_email(email):
        return "Please enter a valid email address."

    if not is_valid_role(role):
        return "Invalid role selected."

    if status is not None and not is_valid_status(status):
        return "Invalid status selected."

    if account_email_exists(email, exclude_account_id=exclude_account_id):
        return "Email already exists."

    if role == "Student" and not course:
        return "Course is required for student accounts."

    return None


def populate_account_fields(account, *, form_or_row, role_value):
    account.first_name = normalize_text(form_or_row.get("first_name"))
    account.middle_name = normalize_optional_student_field(form_or_row.get("middle_name"))
    account.last_name = normalize_text(form_or_row.get("last_name"))
    account.suffix = normalize_optional_student_field(form_or_row.get("suffix"))
    account.email = normalize_email(form_or_row.get("email"))

    set_account_role(account, role_value)

    if role_value == "Student":
        account.year_level = normalize_optional_student_field(form_or_row.get("year_level"))
        account.course = normalize_optional_student_field(form_or_row.get("course"))
    else:
        account.year_level = None
        account.course = None


def get_active_context():
    active_settings = get_active_settings_record()
    return {
        "active_semester": active_settings.active_semester if active_settings else "Not Set",
        "active_school_year": active_settings.active_school_year if active_settings else "Not Set",
        "active_course": getattr(active_settings, "active_course", "Not Set") if active_settings else "Not Set",
    }


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

    active_context = get_active_context()

    data = {
        "total_active_accounts": total_active,
        "total_unactivated_accounts": total_inactive,
        "total_active_students": total_active_students,
        "total_active_finance": total_active_finance,
        "total_active_admin": total_active_admin,
        "admin_user": get_admin_display_name(),
        "active_semester": active_context["active_semester"],
        "active_school_year": active_context["active_school_year"],
        "active_course": active_context["active_course"],
    }

    log_action(get_admin_display_name(), "Viewed dashboard")
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

    active_context = get_active_context()

    log_action(get_admin_display_name(), "Viewed accounts page")

    return render_template(
        "admin/accounts.html",
        accounts=pagination.items,
        pagination=pagination,
        page=page,
        total_pages=pagination.pages,
        admin_user=get_admin_display_name(),
        search=search,
        role_filter=role_filter,
        status_filter=status_filter,
        active_semester=active_context["active_semester"],
        active_school_year=active_context["active_school_year"],
        active_course=active_context["active_course"],
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
        email = normalize_email(request.form.get("email"))
        role = normalize_role(request.form.get("role"))
        year_level = normalize_optional_student_field(request.form.get("year_level"))
        course = normalize_optional_student_field(request.form.get("course"))

        validation_error = validate_account_payload(
            first_name=first_name,
            last_name=last_name,
            email=email,
            role=role,
            status="Active",
            course=course,
        )
        if validation_error:
            flash(validation_error, "danger")
            return render_template("admin/add_new_account.html", active_courses=active_courses)

        password = generate_random_password(last_name)

        account = Account(
            first_name=first_name,
            middle_name=middle_name or None,
            last_name=last_name,
            suffix=suffix or None,
            email=email,
        )

        set_account_role(account, role)
        set_account_status(account, "Active")

        if role == "Student":
            account.year_level = year_level
            account.course = course
        else:
            account.year_level = None
            account.course = None

        account.set_password(password)

        try:
            db.session.add(account)
            db.session.commit()

            log_action(
                get_admin_display_name(),
                f"Added new account: {get_full_name(account)} ({email})",
            )

            session["generated_account_password"] = {
                "email": email,
                "password": password,
            }

            return redirect(url_for("admin.show_generated_password"))

        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to create new account for email: %s", email)
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

    if request.method == "POST":
        try:
            if "reset_password" in request.form:
                new_password = generate_random_password(account.last_name)
                account.set_password(new_password)
                db.session.commit()

                session["generated_account_password"] = {
                    "email": account.email or "",
                    "password": new_password,
                }

                flash("Password reset successfully.", "success")
                log_action(
                    get_admin_display_name(),
                    f"Reset password for {get_full_name(account)}",
                )
                return redirect(url_for("admin.show_generated_password"))

            first_name = normalize_text(request.form.get("first_name"))
            middle_name = normalize_text(request.form.get("middle_name"))
            last_name = normalize_text(request.form.get("last_name"))
            suffix = normalize_text(request.form.get("suffix"))
            email = normalize_email(request.form.get("email"))
            role_value = normalize_role(request.form.get("role"))
            status_value = normalize_status(request.form.get("status"))
            year_level = normalize_optional_student_field(request.form.get("year_level"))
            course = normalize_optional_student_field(request.form.get("course"))

            validation_error = validate_account_payload(
                first_name=first_name,
                last_name=last_name,
                email=email,
                role=role_value,
                status=status_value,
                course=course,
                exclude_account_id=account.id,
            )
            if validation_error:
                flash(validation_error, "danger")
                return redirect(url_for("admin.edit_account", account_id=account.id))

            if account.id == admin.id and status_value != "Active":
                flash("You cannot deactivate your own admin account.", "danger")
                return redirect(url_for("admin.edit_account", account_id=account.id))

            if account.id == admin.id and role_value != "Admin":
                flash("You cannot change your own admin role.", "danger")
                return redirect(url_for("admin.edit_account", account_id=account.id))

            account.first_name = first_name
            account.middle_name = middle_name or None
            account.last_name = last_name
            account.suffix = suffix or None
            account.email = email

            set_account_role(account, role_value)
            set_account_status(account, status_value)

            if role_value == "Student":
                account.year_level = year_level
                account.course = course
            else:
                account.year_level = None
                account.course = None

            db.session.commit()

            flash("Account updated successfully.", "success")
            log_action(
                get_admin_display_name(),
                f"Updated account: {get_full_name(account)}",
            )

            return redirect(url_for("admin.edit_account", account_id=account.id))

        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update account ID %s", account_id)
            flash("Failed to update account.", "danger")
            return redirect(url_for("admin.edit_account", account_id=account.id))

    active_courses = ActiveCourse.query.order_by(ActiveCourse.name.asc()).all()
    return render_template(
        "admin/edit_account.html",
        account=account,
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
                get_admin_display_name(),
                f"Changed semester from '{old_semester}' to '{active_settings.active_semester}'",
            )
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update active semester")
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
                get_admin_display_name(),
                f"Changed school year from '{old_year}' to '{active_settings.active_school_year}'",
            )
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to update active school year")
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
                get_admin_display_name(),
                f"Added new course '{course_name}'",
            )
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to add course: %s", course_name)
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
            get_admin_display_name(),
            f"Deleted course '{course_name}'",
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete course ID %s", course_id)
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

        df.columns = [str(col).strip().lower() for col in df.columns]
        df = df.fillna("")

        existing_emails = {
            email.lower()
            for (email,) in db.session.query(Account.email).all()
            if email
        }

        created_count = 0
        skipped_count = 0
        created_names = []

        for _, row in df.iterrows():
            first_name = normalize_text(row.get("first_name"))
            middle_name = normalize_text(row.get("middle_name"))
            last_name = normalize_text(row.get("last_name"))
            suffix = normalize_text(row.get("suffix"))
            email = normalize_email(row.get("email"))
            role = normalize_role(row.get("role")) or "Student"
            status = normalize_status(row.get("status")) or "Inactive"
            year_level = normalize_optional_student_field(row.get("year_level"))
            course = normalize_optional_student_field(row.get("course"))

            if not email or email in existing_emails:
                skipped_count += 1
                continue

            validation_error = validate_account_payload(
                first_name=first_name,
                last_name=last_name,
                email=email,
                role=role,
                status=status,
                course=course,
            )
            if validation_error:
                skipped_count += 1
                continue

            password = generate_random_password(last_name)

            account = Account(
                first_name=first_name,
                middle_name=middle_name or None,
                last_name=last_name,
                suffix=suffix or None,
                email=email,
            )

            set_account_role(account, role)
            set_account_status(account, status)

            if role == "Student":
                account.year_level = year_level
                account.course = course
            else:
                account.year_level = None
                account.course = None

            account.set_password(password)

            db.session.add(account)
            existing_emails.add(email)
            created_count += 1
            created_names.append(get_full_name(account))

        db.session.commit()

        if created_count:
            flash(f"Successfully uploaded {created_count} account(s).", "success")
            log_action(
                get_admin_display_name(),
                f"Uploaded accounts: {', '.join(created_names)}",
            )
        else:
            flash("No new accounts were added.", "info")

        if skipped_count:
            flash(f"Skipped {skipped_count} invalid or duplicate row(s).", "warning")

        return redirect(url_for("admin.accounts"))

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Failed to upload accounts file: %s", filename)
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
        get_admin_display_name(),
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

    log_action(get_admin_display_name(), "Exported all accounts to CSV")

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

    log_action(get_admin_display_name(), "Exported all accounts to Excel")

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

    generated = session.get("generated_account_password", {}) or {}
    password = generated.get("password", "")
    email = generated.get("email", "")

    if not password and not email:
        flash("No generated password is available.", "warning")
        return redirect(url_for("admin.accounts"))

    return render_template(
        "admin/generated_password.html",
        password=password,
        email=email,
        admin_user=get_admin_display_name(),
    )


# =====================================================
# LOGOUT
# =====================================================
@admin_bp.route("/logout", methods=["GET", "POST"])
@require_role("Admin")
def logout():
    user_name = get_admin_display_name()
    log_action(user_name, "Logged out")
    session.clear()
    flash("You have been logged out.", "danger")
    return redirect(url_for("login"))