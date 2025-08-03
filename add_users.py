# add_users.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from main import UserRecord  # импортируем модель из main.py

DB_URL = "sqlite:///users.db"
engine = create_engine(DB_URL)
SessionLocal = sessionmaker(bind=engine)

users_to_add = [
    405262718,
    350734787,
    777582106,
    354779099,
    497060934,
    867840316,
    744941849,
    1014477509,
    405971111,
]

with SessionLocal() as session:
    for uid in users_to_add:
        user = session.query(UserRecord).filter_by(user_id=uid).first()
        if user:
            user.status = "approved"
        else:
            session.add(UserRecord(user_id=uid, username="N/A", status="approved"))
    session.commit()

print("✅ Пользователи добавлены и одобрены")
