from collections import defaultdict
from datetime import datetime
import calendar
import csv
import io
import json

import pandas as pd
from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from functools import wraps
from sqlalchemy import func, literal
from sqlalchemy.orm import joinedload

from models import db, Account, PromissoryRequest, ActiveSettings, ActiveCourse, SystemLog


finance_bp = Blueprint(
    "finance",
    __name__,
    url_prefix="/finance",
    template_folder="templates",
)


# ---------------------------------------------------
# HELPERS
# ---------------------------------------------------
def require_role(role=None):
    def wrapper(func_to_wrap):
        @wraps(func_to_wrap)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))

            if role and session.get("role") != role:
                flash("Access denied.", "danger")
                return redirect(url_for("login"))

            return func_to_wrap(*args, **kwargs)

        return decorated_function

    return wrapper


def get_finance_user_name():
    return session.get("user_name", "Finance User")


def get_full_name(acc):
    if not acc:
        return "N/A"

    return " ".join(
        part.strip()
        for part in [
            getattr(acc, "first_name", "") or "",
            getattr(acc, "middle_name", "") or "",
            getattr(acc, "last_name", "") or "",
            getattr(acc, "suffix", "") or "",
        ]
        if part and part.strip()
    ).strip() or "N/A"


def get_active_settings():
    settings = ActiveSettings.query.first()
    return (
        settings.active_semester if settings else "Not Set",
        settings.active_school_year if settings else "Not Set",
    )


def log_action(user_name, action):
    """Safely record finance logs without breaking the main request."""
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


def normalize_arg(name, default=""):
    return (request.args.get(name, default) or "").strip()


def safe_school_year_sort(value):
    try:
        return int(str(value).split("-")[0])
    except (ValueError, TypeError, AttributeError):
        return -1


def build_export_file(data, export_format, filename_base, sheet_name):
    if export_format == "csv":
        output = io.StringIO()
        if data:
            writer = csv.DictWriter(output, fieldnames=list(data[0].keys()))
            writer.writeheader()
            writer.writerows(data)
        else:
            writer = csv.writer(output)
            writer.writerow(["No data available"])

        byte_stream = io.BytesIO(output.getvalue().encode("utf-8"))
        byte_stream.seek(0)
        return send_file(
            byte_stream,
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"{filename_base}.csv",
        )

    if export_format == "excel":
        df = pd.DataFrame(data if data else [{"No data": "No data available"}])
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name=sheet_name)
        output.seek(0)
        return send_file(
            output,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"{filename_base}.xlsx",
        )

    return None


def build_promissory_export_rows(results):
    return [
        {
            "Student Name": get_full_name(r.student),
            "Course": r.course or "N/A",
            "Year Level": r.year_level or "N/A",
            "Semester": r.semester or "N/A",
            "Semester Type": r.semester_type or "N/A",
            "School Year": r.school_year or "N/A",
            "Status": r.status or "N/A",
            "Date Submitted": r.requested_at.strftime("%b %d, %Y") if r.requested_at else "N/A",
        }
        for r in results
    ]


def apply_promissory_filters(
    query,
    search="",
    status_filter="",
    semester_filter="",
    semester_type_filter="",
    school_year_filter="",
    course_filter="",
):
    if search:
        term = f"%{search}%"
        full_name_expr = (
            Account.first_name
            + literal(" ")
            + func.coalesce(Account.last_name, "")
        )
        query = query.filter(
            full_name_expr.ilike(term)
            | Account.first_name.ilike(term)
            | Account.last_name.ilike(term)
        )

    if status_filter and status_filter != "All":
        query = query.filter(PromissoryRequest.status == status_filter)

    if semester_filter:
        query = query.filter(PromissoryRequest.semester == semester_filter)

    if semester_type_filter:
        query = query.filter(PromissoryRequest.semester_type == semester_type_filter)

    if school_year_filter:
        query = query.filter(PromissoryRequest.school_year == school_year_filter)

    if course_filter:
        query = query.filter(PromissoryRequest.course == course_filter)

    return query


# ---------------------------------------------------
# DASHBOARD
# ---------------------------------------------------
@finance_bp.route("/dashboard")
@require_role("Finance")
def dashboard():
    user_name = get_finance_user_name()
    active_semester, active_school_year = get_active_settings()

    base_query = PromissoryRequest.query.filter_by(
        semester=active_semester,
        school_year=active_school_year,
    )

    data = {
        "total_promissory": base_query.count(),
        "total_pending": base_query.filter_by(status="Pending").count(),
        "total_approved": base_query.filter_by(status="Approved").count(),
        "total_rejected": base_query.filter_by(status="Rejected").count(),
    }

    recent_requests_raw = (
        base_query.options(joinedload(PromissoryRequest.student))
        .filter(PromissoryRequest.status == "Pending")
        .order_by(PromissoryRequest.requested_at.desc())
        .limit(5)
        .all()
    )

    recent_requests = [
        {
            "student_name": get_full_name(r.student),
            "course": r.course,
            "semester": r.semester,
            "semester_type": r.semester_type,
            "date_submitted": r.requested_at,
            "status": r.status,
        }
        for r in recent_requests_raw
    ]

    log_action(user_name, "Viewed finance dashboard")

    return render_template(
        "finance/dashboard.html",
        finance_user=user_name,
        active_semester=active_semester,
        active_school_year=active_school_year,
        data=data,
        recent_requests=recent_requests,
    )


# ---------------------------------------------------
# PROMISSORY LIST
# ---------------------------------------------------
@finance_bp.route("/promissory-notes")
@require_role("Finance")
def promissory_notes():
    user_name = get_finance_user_name()
    active_semester, active_school_year = get_active_settings()

    all_courses = [c.name for c in ActiveCourse.query.order_by(ActiveCourse.name).all()]
    all_semesters = [
        s[0]
        for s in db.session.query(PromissoryRequest.semester)
        .distinct()
        .order_by(PromissoryRequest.semester)
        .all()
        if s[0]
    ]
    all_semester_types = [
        s[0]
        for s in db.session.query(PromissoryRequest.semester_type)
        .distinct()
        .order_by(PromissoryRequest.semester_type)
        .all()
        if s[0]
    ]
    all_school_years = [
        s[0]
        for s in db.session.query(PromissoryRequest.school_year)
        .distinct()
        .order_by(PromissoryRequest.school_year.desc())
        .all()
        if s[0]
    ]

    search = normalize_arg("search")
    status_filter = normalize_arg("status", "Pending").capitalize()
    semester_filter = normalize_arg("semester", active_semester)
    semester_type_filter = normalize_arg("semester_type")
    school_year_filter = normalize_arg("school_year", active_school_year)
    course_filter = normalize_arg("course")
    export_format = normalize_arg("export")
    page = request.args.get("page", 1, type=int)
    per_page = 8

    query = PromissoryRequest.query.join(
        Account,
        PromissoryRequest.student_id == Account.id,
    )

    query = apply_promissory_filters(
        query=query,
        search=search,
        status_filter=status_filter,
        semester_filter=semester_filter,
        semester_type_filter=semester_type_filter,
        school_year_filter=school_year_filter,
        course_filter=course_filter,
    )

    ordered_query = query.options(joinedload(PromissoryRequest.student)).order_by(
        PromissoryRequest.requested_at.desc()
    )

    if export_format in {"csv", "excel"}:
        results = ordered_query.all()
        log_action(
            user_name,
            f"Exported promissory requests ({export_format.upper()}) with filters: "
            f"status={status_filter}, semester={semester_filter}, semester_type={semester_type_filter}, "
            f"school_year={school_year_filter}, course={course_filter}",
        )
        export_rows = build_promissory_export_rows(results)
        return build_export_file(
            export_rows,
            export_format,
            "promissory_requests",
            "Promissory Requests",
        )

    pagination = ordered_query.paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        "finance/promissory_notes.html",
        promissory_requests=pagination.items,
        finance_user=user_name,
        search=search,
        status_filter=status_filter,
        selected_semester=semester_filter,
        selected_semester_type=semester_type_filter,
        selected_school_year=school_year_filter,
        selected_course=course_filter,
        all_courses=all_courses,
        all_semesters=all_semesters,
        all_semester_types=all_semester_types,
        all_school_years=all_school_years,
        active_semester=active_semester,
        active_school_year=active_school_year,
        pagination=pagination,
        total_pages=pagination.pages,
    )


# ---------------------------------------------------
# ALL PROMISSORY ANALYTICS
# ---------------------------------------------------
@finance_bp.route("/all-promissory")
@require_role("Finance")
def all_promissory():
    user_name = get_finance_user_name()
    active_semester, active_school_year = get_active_settings()

    course_filter = normalize_arg("course") or None
    semester_filter = normalize_arg("semester", active_semester)
    if semester_filter.lower() == "all":
        semester_filter = None

    status_filter = normalize_arg("status", "all").lower()
    semester_type_filter = normalize_arg("semester_type") or None
    school_year_filter = normalize_arg("school_year", active_school_year)
    if school_year_filter.lower() == "all":
        school_year_filter = None

    all_students_query = Account.query.filter(Account._role == "Student")
    if course_filter:
        all_students_query = all_students_query.filter(Account.course == course_filter)

    all_students = all_students_query.all()
    total_students = len(all_students)

    requests_query = PromissoryRequest.query.join(
        Account,
        PromissoryRequest.student_id == Account.id,
    )

    if course_filter:
        requests_query = requests_query.filter(PromissoryRequest.course == course_filter)
    if semester_filter:
        requests_query = requests_query.filter(PromissoryRequest.semester == semester_filter)
    if semester_type_filter:
        requests_query = requests_query.filter(PromissoryRequest.semester_type == semester_type_filter)
    if school_year_filter:
        requests_query = requests_query.filter(PromissoryRequest.school_year == school_year_filter)
    if status_filter != "all":
        requests_query = requests_query.filter(PromissoryRequest.status.ilike(status_filter))

    promissory_requests = requests_query.options(
        joinedload(PromissoryRequest.student)
    ).all()

    export_format = normalize_arg("export")
    if export_format in {"csv", "excel"}:
        export_rows = build_promissory_export_rows(promissory_requests)
        log_action(
            user_name,
            f"Exported all promissory analytics ({export_format.upper()}) with filters: "
            f"course={course_filter or 'All'}, semester={semester_filter or 'All'}, "
            f"semester_type={semester_type_filter or 'All'}, school_year={school_year_filter or 'All'}, "
            f"status={status_filter}",
        )
        return build_export_file(
            export_rows,
            export_format,
            "promissory_requests",
            "Promissory Requests",
        )

    unique_student_ids = {r.student_id for r in promissory_requests}
    total_requested = len(unique_student_ids)
    selected_status = status_filter

    courses = [
        c[0]
        for c in db.session.query(PromissoryRequest.course).distinct().all()
        if c[0]
    ]
    semesters = [
        s[0]
        for s in db.session.query(PromissoryRequest.semester).distinct().all()
        if s[0]
    ]
    semester_types = [
        s[0]
        for s in db.session.query(PromissoryRequest.semester_type).distinct().all()
        if s[0]
    ]
    school_years = sorted(
        [
            y[0]
            for y in db.session.query(PromissoryRequest.school_year).distinct().all()
            if y[0]
        ],
        key=safe_school_year_sort,
    )

    monthly_course_counts = defaultdict(lambda: [0] * 12)
    course_student_counts = defaultdict(set)

    for req in promissory_requests:
        if req.requested_at and req.course:
            idx = req.requested_at.month - 1
            monthly_course_counts[req.course][idx] += 1
            course_student_counts[req.course].add(req.student_id)

    top_course = (
        max(monthly_course_counts.items(), key=lambda x: sum(x[1]))[0]
        if monthly_course_counts
        else "N/A"
    )

    months = [calendar.month_abbr[i + 1] for i in range(12)]
    top_course_monthly = monthly_course_counts.get(top_course, [0] * 12)

    courses_sorted = [
        course
        for course, _ in sorted(
            ((course, len(students)) for course, students in course_student_counts.items()),
            key=lambda x: x[1],
        )
    ]

    counts_sorted = [len(course_student_counts[c]) for c in courses_sorted]
    totals_sorted = [sum(1 for s in all_students if s.course == c) for c in courses_sorted]

    percentages_sorted = [
        round((counts_sorted[i] / totals_sorted[i]) * 100, 2) if totals_sorted[i] else 0
        for i in range(len(courses_sorted))
    ]

    sorted_percentages = sorted(
        zip(courses_sorted, percentages_sorted),
        key=lambda x: x[1],
        reverse=True,
    )

    if sorted_percentages:
        percentage_courses_sorted, percentages_sorted_desc = zip(*sorted_percentages)
        percentage_courses_sorted = list(percentage_courses_sorted)
        percentages_sorted_desc = list(percentages_sorted_desc)
    else:
        percentage_courses_sorted, percentages_sorted_desc = [], []

    bar_data = {
        "labels": ["Total Students", "Requested Promissory"],
        "values": [total_students, total_requested],
    }

    log_action(user_name, "Viewed all promissory analytics")

    return render_template(
        "finance/all_promissory.html",
        finance_user=user_name,
        courses=courses,
        semesters=semesters,
        semester_types=semester_types,
        school_years=school_years,
        selected_course=course_filter or "",
        selected_semester=semester_filter or "",
        selected_semester_type=semester_type_filter or "",
        selected_school_year=school_year_filter or "",
        selected_status=selected_status,
        total_students=total_students,
        total_requested=total_requested,
        active_semester=active_semester,
        active_school_year=active_school_year,
        bar_data=json.dumps(bar_data),
        top_course=top_course,
        months=json.dumps(months),
        monthly_counts=json.dumps(top_course_monthly),
        courses_sorted=json.dumps(courses_sorted),
        counts_sorted=json.dumps(counts_sorted),
        totals_sorted=json.dumps(totals_sorted),
        percentages_sorted=json.dumps(percentages_sorted),
        percentage_courses_sorted=json.dumps(percentage_courses_sorted),
        percentages_sorted_desc=json.dumps(percentages_sorted_desc),
    )


# ---------------------------------------------------
# STUDENTS PROMISSORY LIST
# ---------------------------------------------------
@finance_bp.route("/students-promissory")
@require_role("Finance")
def students_promissory():
    user_name = get_finance_user_name()
    page = request.args.get("page", 1, type=int)
    per_page = 10
    export_format = normalize_arg("export")

    active_semester, active_school_year = get_active_settings()

    all_courses = [c.name for c in ActiveCourse.query.order_by(ActiveCourse.name).all()]
    all_semesters = [
        s[0]
        for s in db.session.query(PromissoryRequest.semester).distinct().all()
        if s[0]
    ]
    all_school_years = [
        s[0]
        for s in db.session.query(PromissoryRequest.school_year).distinct().all()
        if s[0]
    ]

    search = normalize_arg("search")
    selected_semester = request.args.get("semester")
    selected_semester_type = request.args.get("semester_type")
    selected_course = request.args.get("course")
    selected_year_level = request.args.get("year_level")
    selected_school_year = request.args.get("school_year")

    if selected_semester is None and "page" not in request.args:
        selected_semester = active_semester
    if selected_school_year is None and "page" not in request.args:
        selected_school_year = active_school_year

    students_query = db.session.query(Account).filter(Account._role == "Student")

    if search:
        term = f"%{search}%"
        full_name_expr = (
            Account.first_name
            + literal(" ")
            + func.coalesce(Account.last_name, "")
        )
        students_query = students_query.filter(
            full_name_expr.ilike(term)
            | Account.first_name.ilike(term)
            | Account.last_name.ilike(term)
        )

    if selected_course:
        students_query = students_query.filter(Account.course == selected_course)

    if selected_year_level:
        students_query = students_query.filter(Account.year_level == selected_year_level)

    requests_query = db.session.query(
        PromissoryRequest.student_id,
        func.count(PromissoryRequest.id).label("requests_count"),
    ).group_by(PromissoryRequest.student_id)

    if selected_semester:
        requests_query = requests_query.filter(PromissoryRequest.semester == selected_semester)
    if selected_semester_type:
        requests_query = requests_query.filter(PromissoryRequest.semester_type == selected_semester_type)
    if selected_course:
        requests_query = requests_query.filter(PromissoryRequest.course == selected_course)
    if selected_school_year:
        requests_query = requests_query.filter(PromissoryRequest.school_year == selected_school_year)

    requests_subq = requests_query.subquery()

    students_query = students_query.outerjoin(
        requests_subq,
        requests_subq.c.student_id == Account.id,
    ).add_columns(
        func.coalesce(requests_subq.c.requests_count, 0).label("requests_count")
    )

    students_query = students_query.filter(
        func.coalesce(requests_subq.c.requests_count, 0) > 0
    ).order_by(
        func.coalesce(requests_subq.c.requests_count, 0).desc(),
        Account.last_name.asc(),
    )

    if export_format in {"csv", "excel"}:
        students_data = students_query.all()
        export_rows = [
            {
                "Student Name": get_full_name(row[0]),
                "Course": row[0].course or "N/A",
                "Year Level": row[0].year_level or "N/A",
                "Semester": selected_semester or "All",
                "Semester Type": selected_semester_type or "All",
                "School Year": selected_school_year or "All",
                "Requests Count": row[1],
            }
            for row in students_data
        ]

        log_action(
            user_name,
            f"Exported students promissory ({export_format.upper()}) with filters: "
            f"search={search}, semester={selected_semester or 'All'}, "
            f"semester_type={selected_semester_type or 'All'}, course={selected_course or 'All'}, "
            f"year_level={selected_year_level or 'All'}, school_year={selected_school_year or 'All'}",
        )

        return build_export_file(
            export_rows,
            export_format,
            "students_promissory",
            "Students Promissory",
        )

    students = students_query.paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        "finance/students_promissory.html",
        students=students,
        finance_user=user_name,
        active_semester=active_semester,
        active_school_year=active_school_year,
        search=search,
        selected_semester=selected_semester,
        selected_semester_type=selected_semester_type,
        selected_course=selected_course,
        selected_year_level=selected_year_level,
        selected_school_year=selected_school_year,
        all_courses=all_courses,
        all_semesters=all_semesters,
        all_school_years=all_school_years,
    )


# ---------------------------------------------------
# UPDATE PROMISSORY
# ---------------------------------------------------
@finance_bp.route("/promissory/<int:promissory_id>/update", methods=["POST"])
@require_role("Finance")
def update_promissory(promissory_id):
    user_name = get_finance_user_name()
    promissory_req = db.session.get(PromissoryRequest, promissory_id)

    if not promissory_req:
        flash("Promissory note not found.", "warning")
        return redirect(url_for("finance.promissory_notes"))

    action = (request.form.get("action") or "").strip().lower()
    comments = (request.form.get("comments") or "").strip()

    if action not in {"approve", "reject"}:
        flash("Invalid action.", "danger")
        return redirect(url_for("finance.view_promissory", promissory_id=promissory_id))

    old_status = promissory_req.status
    promissory_req.status = "Approved" if action == "approve" else "Rejected"
    promissory_req.comments = comments
    promissory_req.updated_at = datetime.utcnow()

    action_label = "approved" if action == "approve" else "rejected"

    try:
        db.session.commit()
        log_action(
            user_name,
            f"{action_label.capitalize()} promissory note ID {promissory_id} "
            f"(from {old_status} to {promissory_req.status})",
        )
        flash(f"Promissory note {action_label} successfully.", "success")
    except Exception:
        db.session.rollback()
        flash("An error occurred while updating the promissory note.", "danger")

    return redirect(url_for("finance.view_promissory", promissory_id=promissory_id))


# ---------------------------------------------------
# VIEW PROMISSORY DETAILS
# ---------------------------------------------------
@finance_bp.route("/promissory/<int:promissory_id>")
@require_role("Finance")
def view_promissory(promissory_id):
    user_name = get_finance_user_name()

    promissory_req = (
        PromissoryRequest.query.options(joinedload(PromissoryRequest.student))
        .filter(PromissoryRequest.id == promissory_id)
        .first()
    )

    if not promissory_req:
        flash("The selected promissory note was not found or has been deleted.", "warning")
        return render_template(
            "finance/promissory_details.html",
            student=None,
            promissory_data=None,
            promissory_history=[],
            finance_user=user_name,
        )

    student = promissory_req.student

    history_query = (
        PromissoryRequest.query.filter_by(student_id=student.id)
        .order_by(PromissoryRequest.requested_at.desc())
        .all()
    )

    promissory_data = {
        "note_id": promissory_req.id,
        "reason_text": promissory_req.reason_text,
        "reason_doc": promissory_req.reason_doc,
        "valid_id": promissory_req.valid_id,
        "comments": promissory_req.comments,
        "semester": promissory_req.semester or "N/A",
        "semester_type": promissory_req.semester_type or "N/A",
        "date_submitted": (
            promissory_req.requested_at.strftime("%b %d, %Y")
            if promissory_req.requested_at
            else "N/A"
        ),
        "status": promissory_req.status or "N/A",
    }

    promissory_history = [
        {
            "date": r.requested_at.strftime("%b %d, %Y") if r.requested_at else "N/A",
            "note_id": r.id,
            "semester": r.semester or "N/A",
            "semester_type": r.semester_type or "N/A",
            "status": r.status,
        }
        for r in history_query
    ]

    log_action(user_name, f"Viewed promissory note ID {promissory_id} details")

    return render_template(
        "finance/promissory_details.html",
        student=student,
        promissory_data=promissory_data,
        promissory_history=promissory_history,
        finance_user=user_name,
    )


# ---------------------------------------------------
# LOGOUT
# ---------------------------------------------------
@finance_bp.route("/logout", methods=["POST"])
def logout():
    user_name = session.get("user_name", "Finance User")
    session.clear()
    flash("You have been logged out.", "danger")
    log_action(user_name, "Logged out")
    return redirect(url_for("login"))