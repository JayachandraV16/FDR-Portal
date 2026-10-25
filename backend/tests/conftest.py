import os

# Settings requires DATABASE_URL; tests never connect to it unless they override it.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ["APP_ENV"] = "test"
