import os
from datetime import timedelta, datetime, timezone
from flask import Flask, redirect, url_for, render_template, request, flash, session
from models import db, Account, SystemLog
from routes.admin_routes import admin_bp
from routes.finance_routes import finance_bp
from routes.student_routes import student_bp


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-production")

    _database_url = os.environ.get("DATABASE_URL", "sqlite:///promissory.db")
    if _database_url.startswith("postgres://"):
        _database_url = _database_url.replace("postgres://", "postgresql://", 1)

    SQLALCHEMY_DATABASE_URI = _database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Recommended session lifetime for inactive users: 30 minutes
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
    SESSION_TIMEOUT_SECONDS = int(os.environ.get("SESSION_TIMEOUT_SECONDS", 3600))  # 30 minutes

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV", "").lower() == "production"

    PREFERRED_URL_SCHEME = "https"

    TEMPLATES_AUTO_RELOAD = env_bool("FLASK_DEBUG", True)

    MAX_LOGIN_ATTEMPTS = int(os.environ.get("MAX_LOGIN_ATTEMPTS", 5))
    LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", 300))  # 5 minutes


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    if env_bool("FLASK_DEBUG", True):
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.jinja_env.auto_reload = True

    app.register_blueprint(admin_bp)
    app.register_blueprint(finance_bp)
    app.register_blueprint(student_bp)

    def utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def log_action(user_name: str, action: str) -> None:
        try:
            log = SystemLog(
                user_name=(str(user_name).strip() if user_name else "Unknown"),
                action=(str(action).strip() if action else "No action provided"),
                timestamp=datetime.now(timezone.utc)
            )
            db.session.add(log)
            db.session.commit()
        except Exception:
            db.session.rollback()

    def get_dashboard_endpoint(role: str | None) -> str | None:
        role_map = {
            "Superadmin": "admin.dashboard",
            "Admin": "admin.dashboard",
            "Finance": "finance.dashboard",
            "Student": "student.dashboard",
        }
        return role_map.get(role)

    def is_account_blocked_by_status(user) -> bool:
        status = str(getattr(user, "status", "") or "").strip().lower()
        return status in {"inactive", "archived"}

    def get_login_attempt_data() -> tuple[int, datetime | None]:
        failed_attempts = session.get("login_failed_attempts", 0)
        lock_until_raw = session.get("login_lock_until")

        if not lock_until_raw:
            return failed_attempts, None

        try:
            lock_until = datetime.fromisoformat(lock_until_raw)
            if lock_until.tzinfo is None:
                lock_until = lock_until.replace(tzinfo=timezone.utc)
            return failed_attempts, lock_until
        except ValueError:
            session.pop("login_lock_until", None)
            return failed_attempts, None

    def clear_login_attempt_data() -> None:
        session.pop("login_failed_attempts", None)
        session.pop("login_lock_until", None)

    def register_failed_login_attempt() -> tuple[int, datetime | None]:
        failed_attempts = int(session.get("login_failed_attempts", 0)) + 1
        session["login_failed_attempts"] = failed_attempts

        lock_until = None
        if failed_attempts >= app.config["MAX_LOGIN_ATTEMPTS"]:
            lock_until = utcnow() + timedelta(seconds=app.config["LOGIN_LOCKOUT_SECONDS"])
            session["login_lock_until"] = lock_until.isoformat()

        return failed_attempts, lock_until

    def is_login_locked() -> tuple[bool, int]:
        _, lock_until = get_login_attempt_data()

        if not lock_until:
            return False, 0

        now = utcnow()
        if now >= lock_until:
            clear_login_attempt_data()
            return False, 0

        remaining_seconds = max(0, int((lock_until - now).total_seconds()))
        return True, remaining_seconds

    def build_display_name(user) -> str:
        parts = [
            getattr(user, "first_name", ""),
            getattr(user, "last_name", "")
        ]
        display_name = " ".join(
            str(part).strip()
            for part in parts
            if part and str(part).strip()
        ).strip()
        return display_name or getattr(user, "email", "Unknown User")

    def is_session_expired() -> bool:
        last_activity_raw = session.get("last_activity")
        if not last_activity_raw:
            return False

        try:
            last_activity = datetime.fromisoformat(last_activity_raw)
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=timezone.utc)
        except ValueError:
            return True

        elapsed_seconds = (utcnow() - last_activity).total_seconds()
        return elapsed_seconds > app.config["SESSION_TIMEOUT_SECONDS"]

    def refresh_session_activity() -> None:
        session["last_activity"] = utcnow().isoformat()

    @app.before_request
    def enforce_session_timeout_and_access():
        if request.endpoint in {None, "static"}:
            return None

        # Auto logout if inactive too long
        if "user_id" in session:
            if is_session_expired():
                user_name = session.get("user_name") or session.get("user_id") or "Unknown"
                log_action(str(user_name), "Auto logged out due to session timeout")
                session.clear()
                flash("Your session has expired due to inactivity. Please log in again.", "warning")
                return redirect(url_for("login"))

            # Refresh timer on every valid request by authenticated user
            refresh_session_activity()
            session.permanent = True

        # Protect admin routes
        if request.path.startswith("/admin"):
            if "user_id" not in session:
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))

            if session.get("role") not in ["Admin", "Superadmin"]:
                flash("Admin access required.", "danger")
                return redirect(url_for("login"))

                return None

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        if request.endpoint in {"login", "logout"} or "user_id" in session:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

        return response

    @app.route("/")
    def home():
        if "user_id" in session:
            endpoint = get_dashboard_endpoint(session.get("role"))
            if endpoint:
                return redirect(url_for(endpoint))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if "user_id" in session:
            endpoint = get_dashboard_endpoint(session.get("role"))
            if endpoint:
                return redirect(url_for(endpoint))

        if request.method == "POST":
            locked, remaining_seconds = is_login_locked()
            if locked:
                minutes = max(1, (remaining_seconds + 59) // 60)
                flash(
                    f"Too many failed login attempts. Please try again in about {minutes} minute(s).",
                    "danger"
                )
                return render_template("login.html"), 429

            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            if not email or not password:
                flash("Email and password are required.", "danger")
                return render_template("login.html"), 400

            user = Account.query.filter_by(email=email).first()

            if not user or not user.check_password(password):
                attempts, lock_until = register_failed_login_attempt()

                if lock_until is not None:
                    log_action(email or "Unknown", "Login locked due to repeated failed attempts")
                    flash(
                        "Too many failed login attempts. Please try again later.",
                        "danger"
                    )
                    return render_template("login.html"), 429

                remaining_attempts = max(0, app.config["MAX_LOGIN_ATTEMPTS"] - attempts)
                if remaining_attempts > 0:
                    flash(
                        f"Invalid email or password. {remaining_attempts} attempt(s) remaining.",
                        "danger"
                    )
                else:
                    flash("Invalid email or password.", "danger")

                log_action(email or "Unknown", "Failed login attempt")
                return render_template("login.html"), 401

            if is_account_blocked_by_status(user):
                log_action(user.email, "Blocked login attempt on inactive/archived account")
                flash(
                    "Your account is not active. Please contact the administrator.",
                    "warning"
                )
                return render_template("login.html"), 403

            endpoint = get_dashboard_endpoint(getattr(user, "role", None))
            if not endpoint:
                log_action(user.email, "Login denied due to unknown role")
                flash("Unknown role. Contact administrator.", "danger")
                return render_template("login.html"), 403

            # Start clean authenticated session
            session.clear()
            clear_login_attempt_data()

            session.permanent = True
            session["user_id"] = user.id
            session["role"] = user.role
            session["user_name"] = build_display_name(user)
            session["logged_in_at"] = utcnow().isoformat()
            session["last_activity"] = utcnow().isoformat()

            log_action(user.email, "Logged in")
            return redirect(url_for(endpoint))

        return render_template("login.html")

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        user_name = session.get("user_name") or session.get("user_id") or "Unknown"

        if "user_id" in session:
            log_action(str(user_name), "Logged out")

        session.clear()
        flash("You have been logged out.", "info")
        return redirect(url_for("login"))

    return app


app = create_app()


if __name__ == "__main__":
    debug_mode = env_bool("FLASK_DEBUG", True)

    with app.app_context():
        db.create_all()

    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", 50001)),
        debug=debug_mode,
        use_reloader=debug_mode,
    )