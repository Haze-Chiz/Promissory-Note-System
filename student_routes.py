import os
from datetime import datetime
from functools import wraps
from pathlib import Path
from uuid import uuid4

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from models import db, Account, PromissoryRequest, ActiveSettings, SystemLog


student_bp = Blueprint(
    "student",
    __name__,
    url_prefix="/student",
    template_folder="templates",
)

ALLOWED_UPLOAD_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}
DEFAULT_UPLOAD_SUBDIR = os.path.join("static", "uploads")
VALID_SEMESTER_TYPES = {"Midterm", "Final", "Prelim", "Pre-Final"}


def require_role(role=None):
    def wrapper(func):
        @wraps(func)
        def decorated(*args, **kwargs):
            user_id = session.get("user_id")
            if not user_id:
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))

            user = db.session.get(Account, user_id)
            if not user:
                session.clear()
                flash("Account not found. Please log in again.", "danger")
                return redirect(url_for("login"))

            if user.status == "Inactive":
                flash("Your account is inactive. Please contact the admin.", "danger")
                return redirect(url_for("student.inactive_notice"))

            if role and user.role != role:
                flash("Access denied.", "danger")
                return redirect(url_for("login"))

            return func(*args, **kwargs)

        return decorated

    return wrapper


def get_current_student():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(Account, user_id)


def get_full_name(acc):
    return " ".join(
        part.strip()
        for part in [
            getattr(acc, "first_name", "") or "",
            getattr(acc, "middle_name", "") or "",
            getattr(acc, "last_name", "") or "",
            getattr(acc, "suffix", "") or "",
        ]
        if part and part.strip()
    ).strip()


def log_action(user_name, action):
    """Safely log student actions without breaking the main request flow."""
    try:
        log = SystemLog(
            user_name=user_name,
            action=action,
            timestamp=datetime.utcnow(),
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


def is_allowed_file(filename):
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[-1].lower()
    return ext in ALLOWED_UPLOAD_EXTENSIONS


def get_upload_root():
    configured_path = current_app.config.get("UPLOAD_FOLDER", DEFAULT_UPLOAD_SUBDIR)
    return Path(configured_path)


def ensure_student_upload_dir(student_id):
    folder = get_upload_root() / f"student_{student_id}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_file(file_obj, student_id, category="reason"):
    if not file_obj or not getattr(file_obj, "filename", None):
        return None

    original_name = secure_filename(file_obj.filename)
    if not original_name:
        raise ValueError("Invalid file name.")

    if not is_allowed_file(original_name):
        raise ValueError("Unsupported file type.")

    ext = original_name.rsplit(".", 1)[-1].lower()
    unique_name = f"{category}_{uuid4().hex}.{ext}"

    student_folder = ensure_student_upload_dir(student_id)
    absolute_path = student_folder / unique_name
    file_obj.save(absolute_path)

    relative_path = Path(student_folder.name) / unique_name
    return str(Path("uploads") / relative_path).replace("\\", "/")


def delete_uploaded_file(relative_path):
    if not relative_path:
        return

    try:
        static_path = Path("static")
        normalized = Path(relative_path)

        if normalized.parts and normalized.parts[0] == "uploads":
            absolute_path = static_path / normalized
        else:
            absolute_path = normalized

        if absolute_path.exists() and absolute_path.is_file():
            absolute_path.unlink()
    except Exception:
        pass


def get_active_term():
    active_settings = ActiveSettings.query.first()
    if active_settings:
        return active_settings.active_semester, active_settings.active_school_year
    return "Not Set", "Not Set"


def normalize_form_value(name):
    return (request.form.get(name) or "").strip()


def is_unique_email(email, current_user_id):
    existing = (
        Account.query.filter(Account.email == email, Account.id != current_user_id)
        .first()
    )
    return existing is None


def is_unique_username(username, current_user_id):
    existing = (
        Account.query.filter(Account.username == username, Account.id != current_user_id)
        .first()
    )
    return existing is None


@student_bp.route("/inactive")
def inactive_notice():
    return render_template("student/inactive_notice.html")


@student_bp.route("/dashboard")
@require_role("Student")
def dashboard():
    student = get_current_student()
    if not student:
        session.clear()
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for("login"))

    total_promissory = PromissoryRequest.query.filter_by(student_id=student.id).count()
    active_promissory = PromissoryRequest.query.filter_by(
        student_id=student.id,
        status="Pending",
    ).count()

    recent_requests = (
        PromissoryRequest.query.filter(
            PromissoryRequest.student_id == student.id,
            PromissoryRequest.status.in_(["Approved", "Rejected"]),
        )
        .order_by(PromissoryRequest.requested_at.desc())
        .limit(5)
        .all()
    )

    rejected_requests = PromissoryRequest.query.filter_by(
        student_id=student.id,
        status="Rejected",
    ).all()

    incomplete_requests = PromissoryRequest.query.filter(
        PromissoryRequest.student_id == student.id,
        (PromissoryRequest.reason_doc.is_(None)) | (PromissoryRequest.valid_id.is_(None)),
    ).all()

    data = {
        "full_name": get_full_name(student),
        "role": student.role,
        "total_promissory": total_promissory,
        "active_promissory": active_promissory,
        "current_time": datetime.now().strftime("%B %d, %Y %I:%M %p"),
    }

    log_action(student.email, "Viewed dashboard")

    return render_template(
        "student/dashboard.html",
        student=student,
        data=data,
        recent_requests=recent_requests,
        rejected_requests=rejected_requests,
        incomplete_requests=incomplete_requests,
    )


@student_bp.route("/request", methods=["GET", "POST"])
@require_role("Student")
def request_promissory():
    student = get_current_student()
    if not student:
        session.clear()
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for("login"))

    semester, school_year = get_active_term()

    if request.method == "POST":
        reason_text = normalize_form_value("reason_text")
        semester_type = normalize_form_value("semester_type")
        reason_file = request.files.get("reason_doc")
        valid_id_file = request.files.get("valid_id")

        if semester_type not in VALID_SEMESTER_TYPES:
            flash("Invalid semester type selected.", "danger")
            return redirect(url_for("student.request_promissory"))

        existing_request = (
            PromissoryRequest.query.filter_by(
                student_id=student.id,
                semester_type=semester_type,
                semester=semester,
                school_year=school_year,
            )
            .order_by(PromissoryRequest.requested_at.desc())
            .first()
        )

        if existing_request:
            if existing_request.status == "Approved":
                flash(
                    f"Your {semester_type} request for {semester} ({school_year}) is already approved.",
                    "info",
                )
                return redirect(url_for("student.request_promissory"))

            if existing_request.status == "Pending":
                flash(
                    f"You already have a pending {semester_type} request for {semester} ({school_year}).",
                    "danger",
                )
                return redirect(url_for("student.request_promissory"))

        if not reason_text and not (reason_file and reason_file.filename):
            flash("Please provide a reason or upload a document.", "danger")
            return redirect(url_for("student.request_promissory"))

        saved_reason_doc = None
        saved_valid_id = None

        try:
            if reason_file and reason_file.filename:
                saved_reason_doc = save_file(reason_file, student.id, "reason")

            if valid_id_file and valid_id_file.filename:
                saved_valid_id = save_file(valid_id_file, student.id, "valid_id")

            new_request = PromissoryRequest(
                student_id=student.id,
                year_level=student.year_level,
                course=student.course,
                email=student.email,
                reason_text=reason_text or None,
                reason_doc=saved_reason_doc,
                valid_id=saved_valid_id,
                semester_type=semester_type,
                semester=semester,
                school_year=school_year,
                status="Pending",
                requested_at=datetime.utcnow(),
            )

            db.session.add(new_request)
            db.session.commit()

            log_action(
                student.email,
                f"Submitted promissory request for {semester_type} {semester} {school_year}",
            )
            flash("Your promissory request has been submitted.", "success")
            return redirect(url_for("student.request_promissory"))

        except ValueError as exc:
            delete_uploaded_file(saved_reason_doc)
            delete_uploaded_file(saved_valid_id)
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("student.request_promissory"))

        except Exception:
            delete_uploaded_file(saved_reason_doc)
            delete_uploaded_file(saved_valid_id)
            db.session.rollback()
            flash("An error occurred while submitting your request.", "danger")
            return redirect(url_for("student.request_promissory"))

    return render_template(
        "student/request.html",
        student=student,
        active_semester=semester,
        active_school_year=school_year,
    )


@student_bp.route("/history")
@require_role("Student")
def history():
    student = get_current_student()
    if not student:
        session.clear()
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for("login"))

    query = PromissoryRequest.query.filter_by(student_id=student.id)

    filterable_fields = ["status", "semester", "semester_type", "school_year"]
    for key in filterable_fields:
        value = normalize_form_value(key) if request.method == "POST" else (request.args.get(key, "").strip())
        if value:
            column = getattr(PromissoryRequest, key, None)
            if column is not None:
                query = query.filter(column == value)

    requests_list = query.order_by(PromissoryRequest.requested_at.desc()).all()

    school_years = [
        sy[0]
        for sy in db.session.query(PromissoryRequest.school_year)
        .filter_by(student_id=student.id)
        .distinct()
        .order_by(PromissoryRequest.school_year.desc())
        .all()
    ]

    log_action(student.email, "Viewed promissory request history")

    return render_template(
        "student/history.html",
        student=student,
        requests=requests_list,
        school_years=school_years,
    )


@student_bp.route("/delete_request/<int:request_id>", methods=["POST"])
@require_role("Student")
def delete_request(request_id):
    student = get_current_student()
    if not student:
        session.clear()
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for("login"))

    req = PromissoryRequest.query.filter_by(
        id=request_id,
        student_id=student.id,
    ).first()

    if not req:
        flash("Request not found.", "danger")
        return redirect(url_for("student.history"))

    if req.status != "Pending":
        flash("Only pending requests can be deleted.", "warning")
        return redirect(url_for("student.history"))

    reason_doc_path = req.reason_doc
    valid_id_path = req.valid_id

    try:
        db.session.delete(req)
        db.session.commit()

        delete_uploaded_file(reason_doc_path)
        delete_uploaded_file(valid_id_path)

        flash("Pending request has been deleted.", "success")
        log_action(student.email, f"Deleted pending promissory request ID {request_id}")
    except Exception:
        db.session.rollback()
        flash("An error occurred while deleting the request.", "danger")

    return redirect(url_for("student.history"))


@student_bp.route("/setup", methods=["GET", "POST"])
@require_role("Student")
def setup():
    student = get_current_student()
    if not student:
        session.clear()
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for("login"))

    db.session.refresh(student)

    if request.method == "POST":
        username = normalize_form_value("username")
        first_name = normalize_form_value("first_name")
        middle_name = normalize_form_value("middle_name")
        last_name = normalize_form_value("last_name")
        email = normalize_form_value("email").lower()
        phone = normalize_form_value("phone")
        password = request.form.get("password", "").strip()

        if username and not is_unique_username(username, student.id):
            flash("Username is already taken.", "danger")
            return redirect(url_for("student.setup"))

        if email and not is_unique_email(email, student.id):
            flash("Email is already in use.", "danger")
            return redirect(url_for("student.setup"))

        try:
            if username:
                student.username = username
            if first_name:
                student.first_name = first_name
            if middle_name:
                student.middle_name = middle_name
            if last_name:
                student.last_name = last_name
            if email:
                student.email = email
            if phone:
                student.phone = phone
            if password:
                student.set_password(password)

            db.session.commit()

            session["user_name"] = get_full_name(student) or student.email

            flash("Profile updated successfully!", "success")
            log_action(student.email, "Updated profile information")
            return redirect(url_for("student.setup"))

        except Exception:
            db.session.rollback()
            flash("An error occurred while updating your profile.", "danger")
            return redirect(url_for("student.setup"))

    return render_template(
        "student/setup.html",
        student=student,
        student_user=session.get("user_name", "Student User"),
    )


@student_bp.route("/view_request/<int:request_id>")
@require_role("Student")
def view_request(request_id):
    student = get_current_student()
    if not student:
        session.clear()
        flash("Session expired. Please log in again.", "warning")
        return redirect(url_for("login"))

    req = PromissoryRequest.query.filter_by(
        id=request_id,
        student_id=student.id,
    ).first()

    if not req:
        flash("Request not found.", "danger")
        return redirect(url_for("student.history"))

    log_action(student.email, f"Viewed promissory request ID {request_id}")
    return render_template(
        "student/view_request.html",
        student=student,
        request=req,
    )


@student_bp.route("/logout", methods=["POST"])
def logout():
    user_name = session.get("user_name", "Student User")
    session.clear()
    flash("You have been logged out.", "danger")
    log_action(user_name, "Logged out")
    return redirect(url_for("login"))