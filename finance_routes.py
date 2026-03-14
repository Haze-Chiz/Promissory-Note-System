from collections import defaultdict
from datetime import datetime
import calendar
import csv
import io
import json

import pandas as pd
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
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


VALID_PROMISSORY_STATUSES = {"Pending", "Approved", "Rejected"}
EXPORT_FORMATS = {"csv", "excel"}
DEFAULT_FINANCE_USER = "Finance User"


# =====================================================
# COMMON HELPERS
# =====================================================
def get_finance_user_name():
    return session.get("user_name", DEFAULT_FINANCE_USER)


def get_finance_user():
    return require_active_user(role="Finance")


def get_full_name(account):
    if not account:
        return "N/A"

    name_parts = [
        getattr(account, "first_name", "") or "",
        getattr(account, "middle_name", "") or "",
        getattr(account, "last_name", "") or "",
        getattr(account, "suffix", "") or "",
    ]
    full_name = " ".join(part.strip() for part in name_parts if part and part.strip())
    return full_name if full_name else "N/A"


def get_active_settings():
    settings = ActiveSettings.query.first()
    if not settings:
        return "Not Set", "Not Set"
    return settings.active_semester or "Not Set", settings.active_school_year or "Not Set"


def normalize_arg(name, default=""):
    return (request.args.get(name, default) or "").strip()


def normalize_optional_filter(value):
    value = (value or "").strip()
    if not value or value.lower() == "all":
        return None
    return value


def normalize_status_filter(value, default="Pending"):
    raw_value = (value or default).strip()
    if not raw_value:
        return default

    normalized = raw_value.capitalize()
    if normalized == "All":
        return "All"

    return normalized if normalized in VALID_PROMISSORY_STATUSES else default


def safe_school_year_sort(value):
    try:
        return int(str(value).split("-")[0])
    except (ValueError, TypeError, AttributeError):
        return -1


def is_ajax_request():
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.is_json
        or "application/json" in (request.headers.get("Accept", "") or "")
    )


def build_action_response(success, message, category="info", redirect_endpoint=None, **redirect_values):
    """
    Supports both traditional form submissions and AJAX/fetch requests.

    - Traditional submit: flash + redirect
    - AJAX/fetch submit: JSON response
    """
    if is_ajax_request():
        payload = {
            "success": success,
            "message": message,
            "category": category,
        }
        if redirect_endpoint:
            payload["redirect_url"] = url_for(redirect_endpoint, **redirect_values)
        status_code = 200 if success else 400
        return jsonify(payload), status_code

    flash(message, category)

    if redirect_endpoint:
        return redirect(url_for(redirect_endpoint, **redirect_values))

    return redirect(request.referrer or url_for("finance.dashboard"))


# =====================================================
# QUERY / FILTER SERVICES
# =====================================================
def build_account_name_search_expression():
    return (
        func.coalesce(Account.first_name, "")
        + literal(" ")
        + func.coalesce(Account.middle_name, "")
        + literal(" ")
        + func.coalesce(Account.last_name, "")
        + literal(" ")
        + func.coalesce(Account.suffix, "")
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
            | Account.middle_name.ilike(term)
            | Account.last_name.ilike(term)
            | Account.suffix.ilike(term)
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
        PromissoryRequest.requested_at.desc(),
        PromissoryRequest.id.desc(),
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
            | Account.middle_name.ilike(term)
            | Account.last_name.ilike(term)
            | Account.suffix.ilike(term)
        )

    if selected_course:
        students_query = students_query.filter(Account.course == selected_course)

    if selected_year_level:
        students_query = students_query.filter(Account.year_level == selected_year_level)

    requests_query = (
        db.session.query(
            PromissoryRequest.student_id,
            func.count(PromissoryRequest.id).label("requests_count"),
        )
        .group_by(PromissoryRequest.student_id)
    )

    if selected_semester:
        requests_query = requests_query.filter(PromissoryRequest.semester == selected_semester)

    if selected_semester_type:
        requests_query = requests_query.filter(
            PromissoryRequest.semester_type == selected_semester_type
        )

    if selected_course:
        requests_query = requests_query.filter(PromissoryRequest.course == selected_course)

    if selected_school_year:
        requests_query = requests_query.filter(
            PromissoryRequest.school_year == selected_school_year
        )

    requests_subq = requests_query.subquery()

    students_query = (
        students_query.outerjoin(
            requests_subq,
            requests_subq.c.student_id == Account.id,
        )
        .add_columns(func.coalesce(requests_subq.c.requests_count, 0).label("requests_count"))
        .filter(func.coalesce(requests_subq.c.requests_count, 0) > 0)
        .order_by(
            func.coalesce(requests_subq.c.requests_count, 0).desc(),
            Account.last_name.asc(),
            Account.first_name.asc(),
        )
    )

    return students_query


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
            mimetype="text/csv; charset=utf-8",
            as_attachment=True,
            download_name=f"{filename_base}.csv",
        )

    if export_format == "excel":
        dataframe = pd.DataFrame(data if data else [{"No data": "No data available"}])
        output = io.BytesIO()

        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            dataframe.to_excel(writer, index=False, sheet_name=sheet_name)

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
            "Date Submitted": (
                item.requested_at.strftime("%b %d, %Y") if item.requested_at else "N/A"
            ),
        }
        for item in results
    ]


def build_students_promissory_export_rows(
    rows,
    selected_semester,
    selected_semester_type,
    selected_school_year,
):
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
def get_distinct_non_empty_values(column, descending=False, custom_sort=None):
    query = db.session.query(column).distinct()
    rows = [row[0] for row in query.all() if row[0]]

    if custom_sort:
        return sorted(rows, key=custom_sort, reverse=descending)

    return sorted(rows, reverse=descending)


def get_filter_lists():
    all_courses = [course.name for course in ActiveCourse.query.order_by(ActiveCourse.name).all()]
    all_semesters = get_distinct_non_empty_values(PromissoryRequest.semester)
    all_semester_types = get_distinct_non_empty_values(PromissoryRequest.semester_type)
    all_school_years = get_distinct_non_empty_values(
        PromissoryRequest.school_year,
        descending=True,
        custom_sort=safe_school_year_sort,
    )

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

    summary_data = {
        "total_promissory": base_query.count(),
        "total_pending": base_query.filter_by(status="Pending").count(),
        "total_approved": base_query.filter_by(status="Approved").count(),
        "total_rejected": base_query.filter_by(status="Rejected").count(),
    }

    recent_requests_raw = (
        base_query.options(joinedload(PromissoryRequest.student))
        .filter(PromissoryRequest.status == "Pending")
        .order_by(PromissoryRequest.requested_at.desc(), PromissoryRequest.id.desc())
        .limit(5)
        .all()
    )

    recent_requests = [
        {
            "student_name": get_full_name(item.student),
            "course": item.course or "N/A",
            "semester": item.semester or "N/A",
            "semester_type": item.semester_type or "N/A",
            "date_submitted": item.requested_at,
            "status": item.status or "N/A",
        }
        for item in recent_requests_raw
    ]

    return summary_data, recent_requests


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

    total_students = all_students_query.count()
    all_students = all_students_query.with_entities(Account.id, Account.course).all()

    requests_query = PromissoryRequest.query.join(
        Account,
        PromissoryRequest.student_id == Account.id,
    )

    if course_filter:
        requests_query = requests_query.filter(PromissoryRequest.course == course_filter)

    if semester_filter:
        requests_query = requests_query.filter(PromissoryRequest.semester == semester_filter)

    if semester_type_filter:
        requests_query = requests_query.filter(
            PromissoryRequest.semester_type == semester_type_filter
        )

    if school_year_filter:
        requests_query = requests_query.filter(PromissoryRequest.school_year == school_year_filter)

    if status_filter and status_filter != "all":
        normalized_status = status_filter.capitalize()
        if normalized_status in VALID_PROMISSORY_STATUSES:
            requests_query = requests_query.filter(PromissoryRequest.status == normalized_status)

    promissory_requests = requests_query.options(joinedload(PromissoryRequest.student)).all()

    unique_student_ids = {item.student_id for item in promissory_requests}
    total_requested = len(unique_student_ids)

    courses = get_distinct_non_empty_values(PromissoryRequest.course)
    semesters = get_distinct_non_empty_values(PromissoryRequest.semester)
    semester_types = get_distinct_non_empty_values(PromissoryRequest.semester_type)
    school_years = get_distinct_non_empty_values(
        PromissoryRequest.school_year,
        descending=True,
        custom_sort=safe_school_year_sort,
    )

    monthly_course_counts = defaultdict(lambda: [0] * 12)
    course_student_counts = defaultdict(set)

    for item in promissory_requests:
        if item.requested_at and item.course:
            month_index = item.requested_at.month - 1
            monthly_course_counts[item.course][month_index] += 1
            course_student_counts[item.course].add(item.student_id)

    top_course = (
        max(monthly_course_counts.items(), key=lambda entry: sum(entry[1]))[0]
        if monthly_course_counts
        else "N/A"
    )

    months = [calendar.month_abbr[index] for index in range(1, 13)]
    top_course_monthly = monthly_course_counts.get(top_course, [0] * 12)

    courses_sorted = [
        course
        for course, _ in sorted(
            (
                (course, len(student_ids))
                for course, student_ids in course_student_counts.items()
            ),
            key=lambda entry: entry[1],
        )
    ]

    counts_sorted = [len(course_student_counts[course]) for course in courses_sorted]

    totals_by_course = defaultdict(int)
    for _, student_course in all_students:
        if student_course:
            totals_by_course[student_course] += 1

    totals_sorted = [totals_by_course.get(course, 0) for course in courses_sorted]

    percentages_sorted = [
        round((counts_sorted[index] / totals_sorted[index]) * 100, 2)
        if totals_sorted[index]
        else 0
        for index in range(len(courses_sorted))
    ]

    sorted_percentages = sorted(
        zip(courses_sorted, percentages_sorted),
        key=lambda entry: entry[1],
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
    _, response = get_finance_user()
    if response:
        return response

    user_name = get_finance_user_name()
    active_semester, active_school_year = get_active_settings()
    summary_data, recent_requests = get_dashboard_summary(active_semester, active_school_year)

    log_action(user_name, "Viewed finance dashboard")

    return render_template(
        "finance/dashboard.html",
        finance_user=user_name,
        active_semester=active_semester,
        active_school_year=active_school_year,
        data=summary_data,
        recent_requests=recent_requests,
    )


@finance_bp.route("/promissory-notes")
@require_role("Finance")
def promissory_notes():
    _, response = get_finance_user()
    if response:
        return response

    user_name = get_finance_user_name()
    active_semester, active_school_year = get_active_settings()
    filters = get_filter_lists()

    search = normalize_arg("search")
    status_filter = normalize_status_filter(normalize_arg("status", "Pending"), default="Pending")
    semester_filter = normalize_optional_filter(normalize_arg("semester", active_semester))
    semester_type_filter = normalize_optional_filter(normalize_arg("semester_type"))
    school_year_filter = normalize_optional_filter(normalize_arg("school_year", active_school_year))
    course_filter = normalize_optional_filter(normalize_arg("course"))
    export_format = normalize_arg("export").lower()

    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 8

    query = build_promissory_notes_query(
        search=search,
        status_filter=status_filter,
        semester_filter=semester_filter,
        semester_type_filter=semester_type_filter,
        school_year_filter=school_year_filter,
        course_filter=course_filter,
    )

    if export_format in EXPORT_FORMATS:
        results = query.all()
        export_rows = build_promissory_export_rows(results)

        log_action(
            user_name,
            f"Exported promissory requests ({export_format.upper()}) with filters: "
            f"status={status_filter}, semester={semester_filter or 'All'}, "
            f"semester_type={semester_type_filter or 'All'}, school_year={school_year_filter or 'All'}, "
            f"course={course_filter or 'All'}",
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
        selected_semester=semester_filter or "",
        selected_semester_type=semester_type_filter or "",
        selected_school_year=school_year_filter or "",
        selected_course=course_filter or "",
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
    _, response = get_finance_user()
    if response:
        return response

    user_name = get_finance_user_name()
    active_semester, active_school_year = get_active_settings()

    course_filter = normalize_optional_filter(normalize_arg("course"))
    semester_filter = normalize_optional_filter(normalize_arg("semester", active_semester))
    status_filter = (normalize_arg("status", "all") or "all").lower()
    semester_type_filter = normalize_optional_filter(normalize_arg("semester_type"))
    school_year_filter = normalize_optional_filter(normalize_arg("school_year", active_school_year))
    export_format = normalize_arg("export").lower()

    analytics = get_all_promissory_analytics_data(
        course_filter=course_filter,
        semester_filter=semester_filter,
        semester_type_filter=semester_type_filter,
        school_year_filter=school_year_filter,
        status_filter=status_filter,
    )

    if export_format in EXPORT_FORMATS:
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
    _, response = get_finance_user()
    if response:
        return response

    user_name = get_finance_user_name()
    active_semester, active_school_year = get_active_settings()

    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 10
    export_format = normalize_arg("export").lower()

    filter_lists = get_filter_lists()
    all_courses = filter_lists["all_courses"]
    all_semesters = filter_lists["all_semesters"]
    all_school_years = filter_lists["all_school_years"]

    search = normalize_arg("search")
    selected_semester = normalize_optional_filter(request.args.get("semester"))
    selected_semester_type = normalize_optional_filter(request.args.get("semester_type"))
    selected_course = normalize_optional_filter(request.args.get("course"))
    selected_year_level = normalize_optional_filter(request.args.get("year_level"))
    selected_school_year = normalize_optional_filter(request.args.get("school_year"))

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

    if export_format in EXPORT_FORMATS:
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
            f"search={search or 'None'}, semester={selected_semester or 'All'}, "
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
    _, response = get_finance_user()
    if response:
        return response

    user_name = get_finance_user_name()
    promissory_req = db.session.get(PromissoryRequest, promissory_id)

    if not promissory_req:
        return build_action_response(
            success=False,
            message="Promissory note not found.",
            category="warning",
            redirect_endpoint="finance.promissory_notes",
        )

    action = (request.form.get("action") or "").strip().lower()
    comments = (request.form.get("comments") or "").strip()

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        action = (payload.get("action") or action).strip().lower()
        comments = (payload.get("comments") or comments).strip()

    if action not in {"approve", "reject"}:
        return build_action_response(
            success=False,
            message="Invalid action.",
            category="danger",
            redirect_endpoint="finance.view_promissory",
            promissory_id=promissory_id,
        )

    old_status = promissory_req.status
    promissory_req.status = "Approved" if action == "approve" else "Rejected"
    promissory_req.comments = comments if comments else None
    promissory_req.updated_at = datetime.utcnow()

    action_label = "approved" if action == "approve" else "rejected"
    success_category = "approve_success" if action == "approve" else "reject_success"

    try:
        db.session.commit()

        log_action(
            user_name,
            f"{action_label.capitalize()} promissory note ID {promissory_id} "
            f"(from {old_status} to {promissory_req.status})",
        )

        return build_action_response(
            success=True,
            message=f"Promissory note {action_label} successfully.",
            category=success_category,
            redirect_endpoint="finance.view_promissory",
            promissory_id=promissory_id,
        )

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to update promissory note ID %s: %s",
            promissory_id,
            exc,
        )

        return build_action_response(
            success=False,
            message="An error occurred while updating the promissory note.",
            category="danger",
            redirect_endpoint="finance.view_promissory",
            promissory_id=promissory_id,
        )


@finance_bp.route("/promissory/<int:promissory_id>")
@require_role("Finance")
def view_promissory(promissory_id):
    _, response = get_finance_user()
    if response:
        return response

    user_name = get_finance_user_name()

    promissory_req = (
        PromissoryRequest.query.options(joinedload(PromissoryRequest.student))
        .filter(PromissoryRequest.id == promissory_id)
        .one_or_none()
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
        .order_by(PromissoryRequest.requested_at.desc(), PromissoryRequest.id.desc())
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
            "date": item.requested_at.strftime("%b %d, %Y") if item.requested_at else "N/A",
            "note_id": item.id,
            "semester": item.semester or "N/A",
            "semester_type": item.semester_type or "N/A",
            "status": item.status or "N/A",
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
    _, response = get_finance_user()
    if response:
        return response

    user_name = get_finance_user_name()
    session.clear()
    flash("You have been logged out.", "danger")
    log_action(user_name, "Logged out")
    return redirect(url_for("login"))