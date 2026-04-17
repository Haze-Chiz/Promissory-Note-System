from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

VALID_ROLES = {"Admin", "Finance", "Student", "Superadmin"}
VALID_STATUSES = {"Active", "Inactive", "Archived"}
VALID_REQUEST_STATUSES = {"Pending", "Approved", "Rejected"}
VALID_SEMESTER_TYPES = {"Prelim", "Midterm", "Final"}


def clean_string(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def clean_email(value):
    value = clean_string(value)
    return value.lower() if value else None


def title_case(value):
    value = clean_string(value)
    return value.title() if value else None


class Account(db.Model):
    __tablename__ = "account"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    first_name = db.Column(db.String(50), nullable=False)
    middle_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50), nullable=False)
    suffix = db.Column(db.String(10))

    email = db.Column(db.String(100), unique=True, nullable=False, index=True)

    _role = db.Column("role", db.String(20), nullable=False, index=True)
    _status = db.Column("status", db.String(20), nullable=False, index=True)

    password_hash = db.Column(db.String(255), nullable=False)
    plain_password = db.Column(db.String(100), nullable=False)

    year_level = db.Column(db.String(20))
    course = db.Column(db.String(100))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime,
                           default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    @property
    def role(self):
        return self._role

    @role.setter
    def role(self, value):
        value = title_case(value)
        if value not in VALID_ROLES:
            raise ValueError("Invalid role")
        self._role = value

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, value):
        value = title_case(value)
        if value not in VALID_STATUSES:
            raise ValueError("Invalid account status")
        self._status = value

    def set_password(self, password):
        password = clean_string(password)

        if not password:
            raise ValueError("Password required")

        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")

        self.password_hash = generate_password_hash(password)
        self.plain_password = password

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def full_name(self):
        parts = [
            clean_string(self.first_name),
            clean_string(self.middle_name),
            clean_string(self.last_name),
            clean_string(self.suffix),
        ]
        return " ".join(p for p in parts if p)

    def __repr__(self):
        return f"<Account id={self.id} email={self.email} role={self.role}>"


@db.event.listens_for(Account, "before_insert")
@db.event.listens_for(Account, "before_update")
def normalize_account(mapper, connection, target):
    target.first_name = title_case(target.first_name)
    target.middle_name = title_case(target.middle_name)
    target.last_name = title_case(target.last_name)
    target.suffix = clean_string(target.suffix)

    target.email = clean_email(target.email)

    target.year_level = clean_string(target.year_level)
    target.course = clean_string(target.course)

    target.plain_password = clean_string(target.plain_password)


class PromissoryRequest(db.Model):
    __tablename__ = "promissory_request"

    id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(
        db.Integer,
        db.ForeignKey("account.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    student = db.relationship("Account", backref="promissory_requests")

    year_level = db.Column(db.String(20), nullable=False)
    course = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)

    reason_text = db.Column(db.Text)
    reason_doc = db.Column(db.String(255))
    valid_id = db.Column(db.String(255))

    semester_type = db.Column(db.String(50), nullable=False, index=True)
    semester = db.Column(db.String(50), nullable=False, index=True)
    school_year = db.Column(db.String(20), nullable=False, index=True)

    status = db.Column(db.String(20), default="Pending", index=True)
    comments = db.Column(db.Text)

    requested_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        index=True
    )

    __table_args__ = (
        db.Index(
            "idx_student_term",
            "student_id",
            "semester_type",
            "semester",
            "school_year",
        ),
    )

    def __repr__(self):
        return f"<PromissoryRequest {self.id} student={self.student_id} status={self.status}>"


@db.event.listens_for(PromissoryRequest, "before_insert")
@db.event.listens_for(PromissoryRequest, "before_update")
def normalize_request(mapper, connection, target):

    target.year_level = clean_string(target.year_level)
    target.course = clean_string(target.course)
    target.email = clean_email(target.email)

    target.reason_text = clean_string(target.reason_text)
    target.reason_doc = clean_string(target.reason_doc)
    target.valid_id = clean_string(target.valid_id)

    target.semester = clean_string(target.semester)
    target.school_year = clean_string(target.school_year)

    semester_type = title_case(target.semester_type)

    if semester_type not in VALID_SEMESTER_TYPES:
        raise ValueError("Invalid semester type")

    target.semester_type = semester_type

    status = title_case(target.status)

    if status not in VALID_REQUEST_STATUSES:
        raise ValueError("Invalid request status")

    target.status = status

    if not target.reason_text and not target.reason_doc:
        raise ValueError("Promissory request requires a reason or document")


class ActiveSettings(db.Model):
    __tablename__ = "active_settings"

    id = db.Column(db.Integer, primary_key=True)

    active_semester = db.Column(db.String(50))
    active_school_year = db.Column(db.String(20))

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<ActiveSettings Semester={self.active_semester}, SY={self.active_school_year}>"


@db.event.listens_for(ActiveSettings, "before_insert")
@db.event.listens_for(ActiveSettings, "before_update")
def normalize_settings(mapper, connection, target):
    target.active_semester = clean_string(target.active_semester)
    target.active_school_year = clean_string(target.active_school_year)


class ActiveCourse(db.Model):
    __tablename__ = "active_course"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(100), unique=True, nullable=False, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ActiveCourse {self.name}>"


@db.event.listens_for(ActiveCourse, "before_insert")
@db.event.listens_for(ActiveCourse, "before_update")
def normalize_course(mapper, connection, target):
    target.name = clean_string(target.name)

    if not target.name:
        raise ValueError("Course name required")


class SystemLog(db.Model):
    __tablename__ = "system_log"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("account.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    user = db.relationship("Account", backref="logs")

    user_name = db.Column(db.String(150))

    action = db.Column(db.String(255), nullable=False, index=True)

    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<SystemLog {self.action} by {self.user_name or 'System'}>"


@db.event.listens_for(SystemLog, "before_insert")
def normalize_log(mapper, connection, target):
    target.user_name = clean_string(target.user_name)
    target.action = clean_string(target.action)

    if not target.action:
        raise ValueError("Log action required")