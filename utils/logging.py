from datetime import datetime
from models import db, SystemLog


def log_action(user_name, action):
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