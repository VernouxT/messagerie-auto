import os
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import SQLModel, Field, create_engine


# 1) On récupère l'adresse de la base dans les variables Render
DATABASE_URL = os.environ["DATABASE_URL"]

# Render fournit souvent une URL qui commence par "postgres://"
# Or certaines libs préfèrent "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


# 2) On crée le "câble" vers la base
engine = create_engine(DATABASE_URL)


# 3) On définit une "table" = un tableau (comme Excel)
class SmsRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    from_number: str
    raw_request: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def init_db():
    # Crée la table si elle n'existe pas
    SQLModel.metadata.create_all(engine)
