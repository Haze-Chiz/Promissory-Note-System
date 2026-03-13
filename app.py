import os
from datetime import timedelta, datetime
from functools import wraps

from flask import Flask, redirect, url_for, render_template, request, flash, session
from models import db, Account, SystemLog
from admin_routes import admin_bp
from finance_routes import finance_bp
from student_routes import student_bp


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-production")

    _database_url = os.environ.get("DATABASE_URL", "sqlite:///promissory.db")
    if _database_url.startswith("postgres://"):
        _database_url = _database_url.replace("postgres://", "postgresql://", 1)

    SQLALCHEMY_DATABASE_URI = _database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV", "").lower() == "production"

    PREFERRED_URL_SCHEME = "https"
    TEMPLATES_AUTO_RELOAD = os.environ.get("FLASK_DEBUG", "false").lower() == "true"


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    # Blueprints already contain their own url_prefix in the route modules.
    app.register_blueprint(admin_bp)
    app.register_blueprint(finance_bp)
    app.register_blueprint(student_bp)

    def login_required(role=None):
        def decorator(func):
            @wraps(func)
            def wrapped(*args, **kwargs):
                if "user_id" not in session:
                    flash("Please log in first.", "warning")
                    return redirect(url_for("login"))

                if role and session.get("role") != role:
                    flash("Access denied.", "danger")
                    return redirect(url_for("login"))

                return func(*args, **kwargs)

            return wrapped
        return decorator

    def log_action(user_name: str, action: str) -> None:
        """Safely record a system log entry."""
        try:
            log = SystemLog(
                user_name=user_name or "Unknown",
                action=action,
                timestamp=datetime.utcnow()
            )
            db.session.add(log)
            db.session.commit()
        except Exception:
            db.session.rollback()

    def get_dashboard_endpoint(role: str):
        role_map = {
            "Admin": "admin.dashboard",
            "Finance": "finance.dashboard",
            "Student": "student.dashboard",
        }
        return role_map.get(role)

    @app.before_request
    def protect_admin_routes():
        """
        Extra safety layer for /admin routes.
        Allows only authenticated Admin users past this point.
        """
        if not request.path.startswith("/admin"):
            return None

        if request.endpoint in {None, "static"}:
            return None

        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))

        if session.get("role") != "Admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("login"))

        return None

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
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            remember = request.form.get("remember") == "on"

            if not email or not password:
                flash("Email and password are required.", "danger")
                return render_template("login.html"), 400

            user = Account.query.filter_by(email=email).first()

            if not user or not user.check_password(password):
                flash("Invalid email or password.", "danger")
                return render_template("login.html"), 401

            if getattr(user, "status", None) == "Inactive":
                flash("Your account is inactive. Please contact the administrator.", "warning")
                return render_template("login.html"), 403

            session.clear()
            session.permanent = remember
            session["user_id"] = user.id
            session["role"] = user.role
            session["user_name"] = " ".join(
                part for part in [user.first_name, user.last_name] if part and str(part).strip()
            ).strip() or user.email

            log_action(user.email, "Logged in")

            endpoint = get_dashboard_endpoint(user.role)
            if not endpoint:
                session.clear()
                flash("Unknown role. Contact administrator.", "danger")
                return redirect(url_for("login"))

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

    @app.route("/finance/dashboard")
    @login_required(role="Finance")
    def finance_dashboard():
        return "<h1>Finance Dashboard</h1>"

    return app


app = create_app()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 50001)),
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
    )