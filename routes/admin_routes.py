import io
import re
import secrets
import string
from functools import wraps

import pandas as pd
from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import case, or_

from models import db, Account, ActiveSettings, ActiveCourse, SystemLog
from utils.logging import log_action


admin_bp = Blueprint(
    "admin",
    __name__,
    url_prefix="/admin",
    template_folder="templates",
)

# =====================================================
# ROLES / STATUSES / SECURITY RULES
# =====================================================
ALL_VALID_ROLES = {"Superadmin", "Admin", "Finance", "Student"}
FORM_VISIBLE_ROLES = {"Admin", "Finance", "Student"}  # Superadmin hidden from forms
VISIBLE_ACCOUNT_ROLES = {"Admin", "Finance", "Student"}  # Superadmin hidden from listing/filtering
VALID_STATUSES = {"Active", "Inactive", "Archived"}

ROLE_HIERARCHY = {
    "Student": 1,
    "Finance": 2,
    "Admin": 3,
    "Superadmin": 4,
}

ADMIN_MANAGEABLE_ROLES = {"Finance", "Student"}
SUPERADMIN_MANAGEABLE_ROLES = {"Admin", "Finance", "Student"}

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# =====================================================
# NORMALIZATION / CONSTANT HELPERS
# =====================================================
def normalize_text(value):
    return (value or "").strip()


def normalize_email(value):
    return normalize_text(value).lower()


def normalize_role(value):
    role_map = {
        "superadmin": "Superadmin",
        "admin": "Admin",
        "finance": "Finance",
        "student": "Student",
    }
    return role_map.get(normalize_text(value).lower(), normalize_text(value))


def normalize_status(value):
    status_map = {
        "active": "Active",
        "inactive": "Inactive",
        "archived": "Archived",
    }
    return status_map.get(normalize_text(value).lower(), normalize_text(value))


def normalize_optional_student_field(value):
    cleaned = normalize_text(value)
    return cleaned or None


def is_valid_role(value):
    return value in ALL_VALID_ROLES


def is_valid_form_visible_role(value):
    return value in FORM_VISIBLE_ROLES


def is_valid_status(value):
    return value in VALID_STATUSES


def is_valid_email(value):
    return bool(value and EMAIL_PATTERN.match(value))


# =====================================================
# AUTH / CURRENT USER HELPERS
# =====================================================
def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(Account, user_id)


def get_account_role(account):
    if not account:
        return None
    return getattr(account, "_role", None) or getattr(account, "role", None)


def get_account_status(account):
    if not account:
        return None
    return getattr(account, "_status", None) or getattr(account, "status", None)


def set_account_role(account, role_value):
    account._role = role_value


def set_account_status(account, status_value):
    account._status = status_value


def get_current_user_role(user=None):
    user = user or get_current_user()
    return get_account_role(user)


def get_current_user_status(user=None):
    user = user or get_current_user()
    return get_account_status(user)


def is_admin_panel_user(user=None):
    role = get_current_user_role(user)
    return role in {"Admin", "Superadmin"}


def get_admin_display_name():
    return session.get("user_name", "Admin User")


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


def require_admin_panel_access(func):
    @wraps(func)
    def decorated(*args, **kwargs):
        user = get_current_user()

        if not user:
            session.clear()
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))

        if not is_admin_panel_user(user):
            log_action(
                get_admin_display_name(),
                f"Unauthorized admin panel access attempt to '{request.path}'",
            )
            flash("You are not authorized to access this page.", "danger")
            return redirect(url_for("login"))

        if get_current_user_status(user) != "Active":
            log_action(
                get_full_name(user),
                f"Inactive or archived account attempted admin access to '{request.path}'",
            )
            session.clear()
            flash("Your account is inactive or archived.", "danger")
            return redirect(url_for("login"))

        g.admin_user = user
        return func(*args, **kwargs)

    return decorated


def get_admin_user():
    return getattr(g, "admin_user", None)


# =====================================================
# OPTIONAL CSRF SUPPORT
# =====================================================
def get_csrf_token():
    token = session.get("_admin_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_admin_csrf_token"] = token
    return token


def is_csrf_protection_enabled():
    return bool(current_app.config.get("ADMIN_REQUIRE_CSRF", False))


def validate_csrf_if_enabled():
    if not is_csrf_protection_enabled():
        return True

    session_token = session.get("_admin_csrf_token")
    form_token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")

    if not session_token or not form_token or session_token != form_token:
        log_action(
            get_admin_display_name(),
            f"CSRF validation failed on '{request.path}'",
        )
        flash("Security validation failed. Please refresh the page and try again.", "danger")
        return False

    return True


@admin_bp.app_context_processor
def inject_admin_template_helpers():
    return {
        "csrf_token": get_csrf_token,
    }


# =====================================================
# ACCOUNT / PERMISSION HELPERS
# =====================================================
def generate_random_password(last_name: str, length: int = 8) -> str:
    clean_last_name = (last_name or "User").strip() or "User"
    alphabet = string.ascii_letters + string.digits
    random_str = "".join(secrets.choice(alphabet) for _ in range(length))
    return f"{clean_last_name}{random_str}"


def account_email_exists(email, exclude_account_id=None):
    query = Account.query.filter(Account.email == email)
    if exclude_account_id is not None:
        query = query.filter(Account.id != exclude_account_id)
    return db.session.query(query.exists()).scalar()


def get_assignable_roles_for_actor(actor):
    actor_role = get_account_role(actor)

    if actor_role == "Superadmin":
        return ["Admin", "Finance", "Student"]

    if actor_role == "Admin":
        return ["Finance", "Student"]

    return []


def get_manageable_roles_for_actor(actor):
    actor_role = get_account_role(actor)

    if actor_role == "Superadmin":
        return SUPERADMIN_MANAGEABLE_ROLES

    if actor_role == "Admin":
        return ADMIN_MANAGEABLE_ROLES

    return set()


def can_assign_role(actor, role_value):
    return role_value in set(get_assignable_roles_for_actor(actor))


def can_manage_target_account(actor, target_account):
    if not actor or not target_account:
        return False

    actor_role = get_account_role(actor)
    target_role = get_account_role(target_account)

    if actor_role == "Superadmin":
        return target_role in SUPERADMIN_MANAGEABLE_ROLES or actor.id == target_account.id

    if actor_role == "Admin":
        return target_role in ADMIN_MANAGEABLE_ROLES

    return False


def get_base_account_query_for_actor(actor):
    actor_role = get_account_role(actor)

    if actor_role == "Superadmin":
        return Account.query.filter(Account._role.in_(VISIBLE_ACCOUNT_ROLES))

    if actor_role == "Admin":
        return Account.query.filter(Account._role.in_(ADMIN_MANAGEABLE_ROLES))

    return Account.query.filter(False)


def validate_account_payload(
    *,
    actor,
    first_name,
    last_name,
    email,
    role,
    status=None,
    course=None,
    exclude_account_id=None,
    allow_superadmin_role=False,
):
    if not first_name or not last_name or not email:
        return "First name, last name, and email are required."

    if not is_valid_email(email):
        return "Please enter a valid email address."

    if allow_superadmin_role:
        if not is_valid_role(role):
            return "Invalid role selected."
    else:
        if not is_valid_form_visible_role(role):
            return "Invalid role selected."

    if not can_assign_role(actor, role) and not (allow_superadmin_role and role == "Superadmin"):
        return "You are not allowed to assign this role."

    if status is not None and not is_valid_status(status):
        return "Invalid status selected."

    if account_email_exists(email, exclude_account_id=exclude_account_id):
        return "Email already exists."

    if role == "Student" and not course:
        return "Course is required for student accounts."

    return None


# =====================================================
# ACTIVE SETTINGS HELPERS
# =====================================================
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


def get_active_context():
    active_settings = get_active_settings_record()
    return {
        "active_semester": active_settings.active_semester if active_settings else "Not Set",
        "active_school_year": active_settings.active_school_year if active_settings else "Not Set",
        "active_course": getattr(active_settings, "active_course", "Not Set") if active_settings else "Not Set",
    }


# =====================================================
# ACCOUNT LIST / FILTER / EXPORT HELPERS
# =====================================================
def get_account_filter_inputs():
    return {
        "search": normalize_text(request.args.get("search")),
        "role_filter": normalize_role(request.args.get("role")),
        "status_filter": normalize_status(request.args.get("status")),
    }


def get_allowed_filter_roles_for_actor(actor):
    if get_account_role(actor) == "Superadmin":
        return ["Admin", "Finance", "Student"]
    if get_account_role(actor) == "Admin":
        return ["Finance", "Student"]
    return []


def apply_account_filters(query, actor, search="", role_filter="", status_filter=""):
    manageable_roles = get_manageable_roles_for_actor(actor)
    actor_role = get_account_role(actor)

    if actor_role == "Superadmin":
        query = query.filter(Account._role.in_(VISIBLE_ACCOUNT_ROLES))
    elif actor_role == "Admin":
        query = query.filter(Account._role.in_(manageable_roles))

    if search:
        like_value = f"%{search}%"
        query = query.filter(
            or_(
                Account.first_name.ilike(like_value),
                Account.middle_name.ilike(like_value),
                Account.last_name.ilike(like_value),
                Account.suffix.ilike(like_value),
                Account.email.ilike(like_value),
                Account.course.ilike(like_value),
                Account.year_level.ilike(like_value),
            )
        )

    if role_filter:
        if actor_role == "Superadmin" and role_filter in VISIBLE_ACCOUNT_ROLES:
            query = query.filter(Account._role == role_filter)
        elif actor_role == "Admin" and role_filter in manageable_roles:
            query = query.filter(Account._role == role_filter)
        else:
            query = query.filter(False)

    if status_filter:
        query = query.filter(Account._status == status_filter)

    return query


def build_filtered_account_query(actor):
    filters = get_account_filter_inputs()

    role_filter = filters["role_filter"]
    status_filter = filters["status_filter"]

    allowed_roles = get_allowed_filter_roles_for_actor(actor)
    if role_filter and role_filter not in allowed_roles:
        role_filter = ""

    if status_filter and not is_valid_status(status_filter):
        status_filter = ""

    query = apply_account_filters(
        get_base_account_query_for_actor(actor),
        actor,
        search=filters["search"],
        role_filter=role_filter,
        status_filter=status_filter,
    )

    return query, {
        "search": filters["search"],
        "role_filter": role_filter,
        "status_filter": status_filter,
    }


def get_accounts_ordering():
    return [
        case(
            (Account._status == "Active", 0),
            (Account._status == "Inactive", 1),
            (Account._status == "Archived", 2),
            else_=3,
        ),
        Account.id.desc(),
    ]


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


def get_scoped_dashboard_counts(actor):
    base_query = get_base_account_query_for_actor(actor)

    return {
        "total_active_accounts": base_query.filter(Account._status == "Active").count(),
        "total_unactivated_accounts": base_query.filter(Account._status == "Inactive").count(),
        "total_archived_accounts": base_query.filter(Account._status == "Archived").count(),
        "total_active_students": base_query.filter(
            Account._status == "Active",
            Account._role == "Student",
        ).count(),
        "total_active_finance": base_query.filter(
            Account._status == "Active",
            Account._role == "Finance",
        ).count(),
        "total_active_admin": base_query.filter(
            Account._status == "Active",
            Account._role == "Admin",
        ).count(),
        "total_active_superadmin": 0,
    }


# =====================================================
# DASHBOARD
# =====================================================
@admin_bp.route("/dashboard")
@require_admin_panel_access
def dashboard():
    admin = get_admin_user()
    active_context = get_active_context()
    counts = get_scoped_dashboard_counts(admin)

    data = {
        **counts,
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
@require_admin_panel_access
def accounts():
    admin = get_admin_user()
    page = request.args.get("page", 1, type=int)
    per_page = 10

    query, filters = build_filtered_account_query(admin)

    pagination = query.order_by(*get_accounts_ordering()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )

    for acc in pagination.items:
        acc.can_edit = can_manage_target_account(admin, acc)

    active_context = get_active_context()
    filter_role_options = get_allowed_filter_roles_for_actor(admin)

    log_action(get_admin_display_name(), "Viewed accounts page")

    return render_template(
        "admin/accounts.html",
        accounts=pagination.items,
        pagination=pagination,
        page=page,
        total_pages=pagination.pages,
        admin_user=get_admin_display_name(),
        search=filters["search"],
        role_filter=filters["role_filter"],
        status_filter=filters["status_filter"],
        filter_role_options=filter_role_options,
        active_semester=active_context["active_semester"],
        active_school_year=active_context["active_school_year"],
        active_course=active_context["active_course"],
    )


# =====================================================
# ADD NEW ACCOUNT
# =====================================================
@admin_bp.route("/add_new_account", methods=["GET", "POST"])
@require_admin_panel_access
def add_new_account():
    admin = get_admin_user()
    active_courses = ActiveCourse.query.order_by(ActiveCourse.name.asc()).all()
    assignable_roles = get_assignable_roles_for_actor(admin)

    if request.method == "POST":
        if not validate_csrf_if_enabled():
            return render_template(
                "admin/add_new_account.html",
                active_courses=active_courses,
                assignable_roles=assignable_roles,
            )

        first_name = normalize_text(request.form.get("firstName"))
        middle_name = normalize_text(request.form.get("middleName"))
        last_name = normalize_text(request.form.get("lastName"))
        suffix = normalize_text(request.form.get("suffix"))
        email = normalize_email(request.form.get("email"))
        role = normalize_role(request.form.get("role"))
        year_level = normalize_optional_student_field(request.form.get("year_level"))
        course = normalize_optional_student_field(request.form.get("course"))

        validation_error = validate_account_payload(
            actor=admin,
            first_name=first_name,
            last_name=last_name,
            email=email,
            role=role,
            status="Active",
            course=course,
        )
        if validation_error:
            flash(validation_error, "danger")
            return render_template(
                "admin/add_new_account.html",
                active_courses=active_courses,
                assignable_roles=assignable_roles,
            )

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
                f"Added new account: {get_full_name(account)} ({email}) with role {role}",
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
            return render_template(
                "admin/add_new_account.html",
                active_courses=active_courses,
                assignable_roles=assignable_roles,
            )

    return render_template(
        "admin/add_new_account.html",
        active_courses=active_courses,
        assignable_roles=assignable_roles,
    )


# =====================================================
# EDIT ACCOUNT
# =====================================================
@admin_bp.route("/edit_account/<int:account_id>", methods=["GET", "POST"])
@require_admin_panel_access
def edit_account(account_id):
    admin = get_admin_user()
    account = db.session.get(Account, account_id)

    if not account:
        flash("Account not found.", "danger")
        return redirect(url_for("admin.accounts"))

    target_role = get_account_role(account)
    actor_role = get_account_role(admin)

    if target_role == "Superadmin":
        log_action(
            get_admin_display_name(),
            f"Attempted to open hidden Superadmin account ID {account_id}",
        )
        flash("Account not found.", "danger")
        return redirect(url_for("admin.accounts"))

    if not can_manage_target_account(admin, account):
        log_action(
            get_admin_display_name(),
            f"Unauthorized account management attempt on account ID {account_id}",
        )
        flash("You are not allowed to manage this account.", "danger")
        return redirect(url_for("admin.accounts"))

    assignable_roles = get_assignable_roles_for_actor(admin)
    role_locked = False
    status_locked = False

    if request.method == "POST":
        if not validate_csrf_if_enabled():
            active_courses = ActiveCourse.query.order_by(ActiveCourse.name.asc()).all()
            return render_template(
                "admin/edit_account.html",
                account=account,
                active_courses=active_courses,
                assignable_roles=assignable_roles,
                role_locked=role_locked,
                status_locked=status_locked,
            )

        try:
            if "reset_password" in request.form:
                if not can_manage_target_account(admin, account):
                    log_action(
                        get_admin_display_name(),
                        f"Unauthorized password reset attempt on account ID {account_id}",
                    )
                    flash("You are not allowed to reset this account password.", "danger")
                    return redirect(url_for("admin.accounts"))

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
            submitted_role = normalize_role(request.form.get("role"))
            submitted_status = normalize_status(request.form.get("status"))
            year_level = normalize_optional_student_field(request.form.get("year_level"))
            course = normalize_optional_student_field(request.form.get("course"))

            role_value = submitted_role
            status_value = submitted_status

            validation_error = validate_account_payload(
                actor=admin,
                first_name=first_name,
                last_name=last_name,
                email=email,
                role=role_value,
                status=status_value,
                course=course,
                exclude_account_id=account.id,
                allow_superadmin_role=False,
            )
            if validation_error:
                flash(validation_error, "danger")
                return redirect(url_for("admin.edit_account", account_id=account.id))

            if account.id == admin.id and status_value != "Active":
                flash("You cannot deactivate or archive your own account.", "danger")
                return redirect(url_for("admin.edit_account", account_id=account.id))

            if actor_role == "Admin" and role_value not in ADMIN_MANAGEABLE_ROLES:
                flash("Admin can only manage Finance and Student accounts.", "danger")
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
                f"Updated account: {get_full_name(account)} | role={role_value} | status={status_value}",
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
        assignable_roles=assignable_roles,
        role_locked=role_locked,
        status_locked=status_locked,
    )


# =====================================================
# SYSTEM LOGS
# =====================================================
@admin_bp.route("/logs")
@require_admin_panel_access
def logs():
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
@require_admin_panel_access
def semester():
    active_settings = get_or_create_active_settings()

    if request.method == "POST":
        if not validate_csrf_if_enabled():
            return redirect(url_for("admin.semester"))

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
@require_admin_panel_access
def school_year():
    active_settings = get_or_create_active_settings()

    if request.method == "POST":
        if not validate_csrf_if_enabled():
            return redirect(url_for("admin.school_year"))

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
@require_admin_panel_access
def course():
    if request.method == "POST":
        if not validate_csrf_if_enabled():
            return redirect(url_for("admin.course"))

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
@require_admin_panel_access
def delete_course(course_id):
    if not validate_csrf_if_enabled():
        return redirect(url_for("admin.course"))

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
@require_admin_panel_access
def upload_accounts():
    if not validate_csrf_if_enabled():
        return redirect(url_for("admin.accounts"))

    admin = get_admin_user()
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
        row_errors = []

        for index, row in df.iterrows():
            row_number = index + 2

            first_name = normalize_text(row.get("first_name"))
            middle_name = normalize_text(row.get("middle_name"))
            last_name = normalize_text(row.get("last_name"))
            suffix = normalize_text(row.get("suffix"))
            email = normalize_email(row.get("email"))
            role = normalize_role(row.get("role")) or "Student"
            status = normalize_status(row.get("status")) or "Inactive"
            year_level = normalize_optional_student_field(row.get("year_level"))
            course = normalize_optional_student_field(row.get("course"))

            if role == "Superadmin":
                skipped_count += 1
                row_errors.append(f"Row {row_number}: Superadmin cannot be imported here.")
                continue

            if not email:
                skipped_count += 1
                row_errors.append(f"Row {row_number}: email is required.")
                continue

            if email in existing_emails:
                skipped_count += 1
                row_errors.append(f"Row {row_number}: duplicate email '{email}'.")
                continue

            if not can_assign_role(admin, role):
                skipped_count += 1
                row_errors.append(f"Row {row_number}: unauthorized role '{role}'.")
                continue

            validation_error = validate_account_payload(
                actor=admin,
                first_name=first_name,
                last_name=last_name,
                email=email,
                role=role,
                status=status,
                course=course,
            )
            if validation_error:
                skipped_count += 1
                row_errors.append(f"Row {row_number}: {validation_error}")
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
            flash(f"Skipped {skipped_count} invalid, duplicate, or unauthorized row(s).", "warning")

        if row_errors:
            preview_limit = 10
            for error_message in row_errors[:preview_limit]:
                flash(error_message, "danger")

            if len(row_errors) > preview_limit:
                flash(
                    f"And {len(row_errors) - preview_limit} more row error(s) not shown.",
                    "warning",
                )

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
@require_admin_panel_access
def download_template():
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
@require_admin_panel_access
def export_csv():
    admin = get_admin_user()
    query, _filters = build_filtered_account_query(admin)
    accounts = query.order_by(*get_accounts_ordering()).all()
    data = build_account_export_rows(accounts)

    df = pd.DataFrame(data)
    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)

    log_action(get_admin_display_name(), "Exported filtered accounts to CSV")

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
@require_admin_panel_access
def export_excel():
    admin = get_admin_user()
    query, _filters = build_filtered_account_query(admin)
    accounts = query.order_by(*get_accounts_ordering()).all()
    data = build_account_export_rows(accounts)

    df = pd.DataFrame(data)
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Accounts")

    output.seek(0)

    log_action(get_admin_display_name(), "Exported filtered accounts to Excel")

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
@require_admin_panel_access
def show_generated_password():
    generated = session.pop("generated_account_password", {}) or {}
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
@require_admin_panel_access
def logout():
    if request.method == "POST" and not validate_csrf_if_enabled():
        return redirect(url_for("admin.dashboard"))

    user_name = get_admin_display_name()
    log_action(user_name, "Logged out")
    session.clear()
    flash("You have been logged out.", "danger")
    return redirect(url_for("login"))