from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import get_engine


def create_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)
