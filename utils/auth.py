from functools import wraps
from flask import flash, redirect, session, url_for
from models import db, Account


BLOCKED_ACCOUNT_STATUSES = {"inactive", "archived"}


def normalize_text(value):
    return str(value).strip().lower() if value is not None else ""


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(Account, user_id)


def get_current_user_by_role(expected_role=None):
    user = get_current_user()
    if not user:
        return None

    if expected_role and normalize_text(user.role) != normalize_text(expected_role):
        return None

    return user


def is_blocked_account(user):
    status = normalize_text(getattr(user, "status", None))
    return status in BLOCKED_ACCOUNT_STATUSES


def handle_blocked_or_missing_user(user, allow_inactive_notice_endpoint=None):
    if not user:
        session.clear()
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    if is_blocked_account(user):
        if allow_inactive_notice_endpoint:
            flash("Your account is inactive. Please contact the administrator.", "danger")
            return redirect(url_for(allow_inactive_notice_endpoint))

        session.clear()
        flash("Your account is inactive. Please contact the administrator.", "danger")
        return redirect(url_for("login"))

    return None


def require_role(role=None, allow_inactive_notice_endpoint=None):
    def wrapper(func):
        @wraps(func)
        def decorated(*args, **kwargs):
            user = get_current_user()

            blocked_response = handle_blocked_or_missing_user(
                user,
                allow_inactive_notice_endpoint=allow_inactive_notice_endpoint,
            )
            if blocked_response:
                return blocked_response

            if role and normalize_text(user.role) != normalize_text(role):
                flash("Access denied.", "danger")
                return redirect(url_for("login"))

            return func(*args, **kwargs)

        return decorated

    return wrapper


def require_active_user(role=None, allow_inactive_notice_endpoint=None):
    user = get_current_user()

    if not user:
        session.clear()
        flash("Session expired. Please log in again.", "warning")
        return None, redirect(url_for("login"))

    if is_blocked_account(user):
        if allow_inactive_notice_endpoint:
            flash("Your account is inactive. Please contact the administrator.", "danger")
            return None, redirect(url_for(allow_inactive_notice_endpoint))

        session.clear()
        flash("Your account is inactive. Please contact the administrator.", "danger")
        return None, redirect(url_for("login"))

    if role and normalize_text(user.role) != normalize_text(role):
        flash("Access denied.", "danger")
        return None, redirect(url_for("login"))

    return user, None