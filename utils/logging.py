from datetime import datetime, timezone
from models import db, SystemLog


def log_action(user_name, action):
    try:
        safe_user_name = str(user_name).strip() if user_name else "Unknown"
        safe_action = str(action).strip() if action else "No action provided"

        log = SystemLog(
            user_name=safe_user_name,
            action=safe_action,
            timestamp=datetime.now(timezone.utc),
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()