from collections import defaultdict
from datetime import datetime
import calendar
import csv
import io
import json

import pandas as pd
from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import func, literal
from sqlalchemy.orm import joinedload

from models import db, Account, PromissoryRequest, ActiveSettings, ActiveCourse
from utils.auth import require_role, require_active_user
from utils.logging import log_action


finance_bp = Blueprint(
    "finance",
    __name__,
    url_prefix="/finance",
    template_folder="templates",
)


# =====================================================
# COMMON HELPERS
# =====================================================
def get_finance_user_name():
    return session.get("user_name", "Finance User")


def get_finance_user():
    finance_user, response = require_active_user(role="Finance")
    return finance_user, response


def get_full_name(account):
    if not account:
        return "N/A"

    return " ".join(
        part.strip()
        for part in [
            getattr(account, "first_name", "") or "",
            getattr(account, "middle_name", "") or "",
            getattr(account, "last_name", "") or "",
            getattr(account, "suffix", "") or "",
        ]
        if part and part.strip()
    ).strip() or "N/A"


def get_active_settings():
    settings = ActiveSettings.query.first()
    return (
        settings.active_semester if settings else "Not Set",
        settings.active_school_year if settings else "Not Set",
    )


def normalize_arg(name, default=""):
    return (request.args.get(name, default) or "").strip()


def safe_school_year_sort(value):
    try:
        return int(str(value).split("-")[0])
    except (ValueError, TypeError, AttributeError):
        return -1


# =====================================================
# QUERY / FILTER SERVICES
# =====================================================
def build_account_name_search_expression():
    return (
        func.coalesce(Account.first_name, "")
        + literal(" ")
        + func.coalesce(Account.last_name, "")
    )


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
        full_name_expr = build_account_name_search_expression()
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


def build_promissory_notes_query(
    search="",
    status_filter="",
    semester_filter="",
    semester_type_filter="",
    school_year_filter="",
    course_filter="",
):
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

    return query.options(joinedload(PromissoryRequest.student)).order_by(
        PromissoryRequest.requested_at.desc()
    )


def build_students_promissory_query(
    search="",
    selected_semester=None,
    selected_semester_type=None,
    selected_course=None,
    selected_year_level=None,
    selected_school_year=None,
):
    students_query = db.session.query(Account).filter(Account._role == "Student")

    if search:
        term = f"%{search}%"
        full_name_expr = build_account_name_search_expression()
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

    return students_query.filter(
        func.coalesce(requests_subq.c.requests_count, 0) > 0
    ).order_by(
        func.coalesce(requests_subq.c.requests_count, 0).desc(),
        Account.last_name.asc(),
    )


# =====================================================
# EXPORT SERVICES
# =====================================================
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
            "Student Name": get_full_name(item.student),
            "Course": item.course or "N/A",
            "Year Level": item.year_level or "N/A",
            "Semester": item.semester or "N/A",
            "Semester Type": item.semester_type or "N/A",
            "School Year": item.school_year or "N/A",
            "Status": item.status or "N/A",
            "Date Submitted": item.requested_at.strftime("%b %d, %Y") if item.requested_at else "N/A",
        }
        for item in results
    ]


def build_students_promissory_export_rows(rows, selected_semester, selected_semester_type, selected_school_year):
    return [
        {
            "Student Name": get_full_name(row[0]),
            "Course": row[0].course or "N/A",
            "Year Level": row[0].year_level or "N/A",
            "Semester": selected_semester or "All",
            "Semester Type": selected_semester_type or "All",
            "School Year": selected_school_year or "All",
            "Requests Count": row[1],
        }
        for row in rows
    ]


# =====================================================
# DATASET HELPERS
# =====================================================
def get_filter_lists():
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

    return {
        "all_courses": all_courses,
        "all_semesters": all_semesters,
        "all_semester_types": all_semester_types,
        "all_school_years": all_school_years,
    }


def get_dashboard_summary(active_semester, active_school_year):
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
            "student_name": get_full_name(item.student),
            "course": item.course,
            "semester": item.semester,
            "semester_type": item.semester_type,
            "date_submitted": item.requested_at,
            "status": item.status,
        }
        for item in recent_requests_raw
    ]

    return data, recent_requests


def get_all_promissory_analytics_data(
    course_filter,
    semester_filter,
    semester_type_filter,
    school_year_filter,
    status_filter,
):
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

    promissory_requests = requests_query.options(joinedload(PromissoryRequest.student)).all()
    unique_student_ids = {item.student_id for item in promissory_requests}
    total_requested = len(unique_student_ids)

    courses = [c[0] for c in db.session.query(PromissoryRequest.course).distinct().all() if c[0]]
    semesters = [s[0] for s in db.session.query(PromissoryRequest.semester).distinct().all() if s[0]]
    semester_types = [s[0] for s in db.session.query(PromissoryRequest.semester_type).distinct().all() if s[0]]
    school_years = sorted(
        [y[0] for y in db.session.query(PromissoryRequest.school_year).distinct().all() if y[0]],
        key=safe_school_year_sort,
    )

    monthly_course_counts = defaultdict(lambda: [0] * 12)
    course_student_counts = defaultdict(set)

    for item in promissory_requests:
        if item.requested_at and item.course:
            month_index = item.requested_at.month - 1
            monthly_course_counts[item.course][month_index] += 1
            course_student_counts[item.course].add(item.student_id)

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
            ((course, len(student_ids)) for course, student_ids in course_student_counts.items()),
            key=lambda x: x[1],
        )
    ]

    counts_sorted = [len(course_student_counts[course]) for course in courses_sorted]
    totals_sorted = [sum(1 for student in all_students if student.course == course) for course in courses_sorted]

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

    return {
        "promissory_requests": promissory_requests,
        "total_students": total_students,
        "total_requested": total_requested,
        "courses": courses,
        "semesters": semesters,
        "semester_types": semester_types,
        "school_years": school_years,
        "bar_data": bar_data,
        "top_course": top_course,
        "months": months,
        "monthly_counts": top_course_monthly,
        "courses_sorted": courses_sorted,
        "counts_sorted": counts_sorted,
        "totals_sorted": totals_sorted,
        "percentages_sorted": percentages_sorted,
        "percentage_courses_sorted": percentage_courses_sorted,
        "percentages_sorted_desc": percentages_sorted_desc,
    }


# =====================================================
# ROUTES
# =====================================================
@finance_bp.route("/dashboard")
@require_role("Finance")
def dashboard():
    finance_user, response = get_finance_user()
    if response:
        return response

    user_name = get_finance_user_name()
    active_semester, active_school_year = get_active_settings()

    data, recent_requests = get_dashboard_summary(active_semester, active_school_year)

    log_action(user_name, "Viewed finance dashboard")

    return render_template(
        "finance/dashboard.html",
        finance_user=user_name,
        active_semester=active_semester,
        active_school_year=active_school_year,
        data=data,
        recent_requests=recent_requests,
    )


@finance_bp.route("/promissory-notes")
@require_role("Finance")
def promissory_notes():
    finance_user, response = get_finance_user()
    if response:
        return response

    user_name = get_finance_user_name()
    active_semester, active_school_year = get_active_settings()

    filters = get_filter_lists()

    search = normalize_arg("search")
    status_filter = normalize_arg("status", "Pending").capitalize()
    semester_filter = normalize_arg("semester", active_semester)
    semester_type_filter = normalize_arg("semester_type")
    school_year_filter = normalize_arg("school_year", active_school_year)
    course_filter = normalize_arg("course")
    export_format = normalize_arg("export")
    page = request.args.get("page", 1, type=int)
    per_page = 8

    query = build_promissory_notes_query(
        search=search,
        status_filter=status_filter,
        semester_filter=semester_filter,
        semester_type_filter=semester_type_filter,
        school_year_filter=school_year_filter,
        course_filter=course_filter,
    )

    if export_format in {"csv", "excel"}:
        results = query.all()
        export_rows = build_promissory_export_rows(results)

        log_action(
            user_name,
            f"Exported promissory requests ({export_format.upper()}) with filters: "
            f"status={status_filter}, semester={semester_filter}, "
            f"semester_type={semester_type_filter}, school_year={school_year_filter}, "
            f"course={course_filter}",
        )

        return build_export_file(
            export_rows,
            export_format,
            "promissory_requests",
            "Promissory Requests",
        )

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

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
        all_courses=filters["all_courses"],
        all_semesters=filters["all_semesters"],
        all_semester_types=filters["all_semester_types"],
        all_school_years=filters["all_school_years"],
        active_semester=active_semester,
        active_school_year=active_school_year,
        pagination=pagination,
        total_pages=pagination.pages,
    )


@finance_bp.route("/all-promissory")
@require_role("Finance")
def all_promissory():
    finance_user, response = get_finance_user()
    if response:
        return response

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

    analytics = get_all_promissory_analytics_data(
        course_filter=course_filter,
        semester_filter=semester_filter,
        semester_type_filter=semester_type_filter,
        school_year_filter=school_year_filter,
        status_filter=status_filter,
    )

    export_format = normalize_arg("export")
    if export_format in {"csv", "excel"}:
        export_rows = build_promissory_export_rows(analytics["promissory_requests"])

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

    log_action(user_name, "Viewed all promissory analytics")

    return render_template(
        "finance/all_promissory.html",
        finance_user=user_name,
        courses=analytics["courses"],
        semesters=analytics["semesters"],
        semester_types=analytics["semester_types"],
        school_years=analytics["school_years"],
        selected_course=course_filter or "",
        selected_semester=semester_filter or "",
        selected_semester_type=semester_type_filter or "",
        selected_school_year=school_year_filter or "",
        selected_status=status_filter,
        total_students=analytics["total_students"],
        total_requested=analytics["total_requested"],
        active_semester=active_semester,
        active_school_year=active_school_year,
        bar_data=json.dumps(analytics["bar_data"]),
        top_course=analytics["top_course"],
        months=json.dumps(analytics["months"]),
        monthly_counts=json.dumps(analytics["monthly_counts"]),
        courses_sorted=json.dumps(analytics["courses_sorted"]),
        counts_sorted=json.dumps(analytics["counts_sorted"]),
        totals_sorted=json.dumps(analytics["totals_sorted"]),
        percentages_sorted=json.dumps(analytics["percentages_sorted"]),
        percentage_courses_sorted=json.dumps(analytics["percentage_courses_sorted"]),
        percentages_sorted_desc=json.dumps(analytics["percentages_sorted_desc"]),
    )


@finance_bp.route("/students-promissory")
@require_role("Finance")
def students_promissory():
    finance_user, response = get_finance_user()
    if response:
        return response

    user_name = get_finance_user_name()
    page = request.args.get("page", 1, type=int)
    per_page = 10
    export_format = normalize_arg("export")

    active_semester, active_school_year = get_active_settings()

    all_courses = [c.name for c in ActiveCourse.query.order_by(ActiveCourse.name).all()]
    all_semesters = [s[0] for s in db.session.query(PromissoryRequest.semester).distinct().all() if s[0]]
    all_school_years = [s[0] for s in db.session.query(PromissoryRequest.school_year).distinct().all() if s[0]]

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

    students_query = build_students_promissory_query(
        search=search,
        selected_semester=selected_semester,
        selected_semester_type=selected_semester_type,
        selected_course=selected_course,
        selected_year_level=selected_year_level,
        selected_school_year=selected_school_year,
    )

    if export_format in {"csv", "excel"}:
        rows = students_query.all()
        export_rows = build_students_promissory_export_rows(
            rows,
            selected_semester,
            selected_semester_type,
            selected_school_year,
        )

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


@finance_bp.route("/promissory/<int:promissory_id>/update", methods=["POST"])
@require_role("Finance")
def update_promissory(promissory_id):
    finance_user, response = get_finance_user()
    if response:
        return response

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


@finance_bp.route("/promissory/<int:promissory_id>")
@require_role("Finance")
def view_promissory(promissory_id):
    finance_user, response = get_finance_user()
    if response:
        return response

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
        "date_submitted": promissory_req.requested_at.strftime("%b %d, %Y") if promissory_req.requested_at else "N/A",
        "status": promissory_req.status or "N/A",
    }

    promissory_history = [
        {
            "date": item.requested_at.strftime("%b %d, %Y") if item.requested_at else "N/A",
            "note_id": item.id,
            "semester": item.semester or "N/A",
            "semester_type": item.semester_type or "N/A",
            "status": item.status,
        }
        for item in history_query
    ]

    log_action(user_name, f"Viewed promissory note ID {promissory_id} details")

    return render_template(
        "finance/promissory_details.html",
        student=student,
        promissory_data=promissory_data,
        promissory_history=promissory_history,
        finance_user=user_name,
    )


@finance_bp.route("/logout", methods=["GET", "POST"])
@require_role("Finance")
def logout():
    finance_user, response = get_finance_user()
    if response:
        return response

    user_name = session.get("user_name", "Finance User")
    session.clear()
    flash("You have been logged out.", "danger")
    log_action(user_name, "Logged out")
    return redirect(url_for("login"))