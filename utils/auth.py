from functools import wraps
from flask import flash, redirect, session, url_for
from models import db, Account


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(Account, user_id)


def get_current_user_by_role(expected_role=None):
    user = get_current_user()
    if not user:
        return None

    if expected_role and user.role != expected_role:
        return None

    return user


def require_role(role=None, allow_inactive_notice_endpoint=None):
    def wrapper(func):
        @wraps(func)
        def decorated(*args, **kwargs):
            user = get_current_user()

            if not user:
                session.clear()
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))

            if getattr(user, "status", None) == "Inactive":
                if allow_inactive_notice_endpoint:
                    return redirect(url_for(allow_inactive_notice_endpoint))

                session.clear()
                flash("Your account is inactive. Please contact the administrator.", "danger")
                return redirect(url_for("login"))

            if role and user.role != role:
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

    if getattr(user, "status", None) == "Inactive":
        if allow_inactive_notice_endpoint:
            flash("Your account is inactive. Please contact the admin.", "danger")
            return None, redirect(url_for(allow_inactive_notice_endpoint))

        session.clear()
        flash("Your account is inactive. Please contact the administrator.", "danger")
        return None, redirect(url_for("login"))

    if role and user.role != role:
        flash("Access denied.", "danger")
        return None, redirect(url_for("login"))

    return user, None