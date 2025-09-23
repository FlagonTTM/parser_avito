import sqlite3
try:
    import psycopg2
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False

from models import Item


class SQLiteDBHandler:
    """Работа с БД sqlite"""
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(SQLiteDBHandler, cls).__new__(cls)
        return cls._instance

    def __init__(self, db_name="database.db"):
        if not hasattr(self, "_initialized"):
            self.db_name = db_name
            self._create_table()
            self._initialized = True

    def _create_table(self):
        """Создает таблицу viewed, если она не существует."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS viewed (
                    id INTEGER PRIMARY KEY
                )
                """
            )
            conn.commit()

    def add_record(self, ad: Item):
        """Добавляет новую запись в таблицу viewed."""

        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO viewed (id) VALUES (?)",
                (ad.id,),
            )
            conn.commit()

    def add_record_from_page(self, ads: list[Item]):
        """Добавляет несколько записей в таблицу viewed."""
        records = [(ad.id,) for ad in ads]

        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT OR IGNORE INTO viewed (id)
                VALUES (?)
                """,
                records,
            )
            conn.commit()

    def record_exists(self, record_id):
        """Проверяет, существует ли запись с заданным id."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM viewed WHERE id = ?",
                (record_id,),
            )
            return cursor.fetchone() is not None


class PostgreSQLDBHandler:
    """Работа с БД PostgreSQL"""
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(PostgreSQLDBHandler, cls).__new__(cls)
        return cls._instance

    def __init__(self, connection_string: str):
        if not hasattr(self, "_initialized"):
            if not POSTGRES_AVAILABLE:
                raise ImportError("psycopg2 не установлен. Установите его: pip install psycopg2-binary")
            self.connection_string = connection_string
            self._create_table()
            self._initialized = True

    def _create_table(self):
        """Создает таблицу viewed, если она не существует."""
        with psycopg2.connect(self.connection_string) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS viewed (
                    id BIGINT PRIMARY KEY
                )
                """
            )
            conn.commit()

    def add_record(self, ad: Item):
        """Добавляет новую запись в таблицу viewed."""
        with psycopg2.connect(self.connection_string) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO viewed (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                (ad.id,),
            )
            conn.commit()

    def add_record_from_page(self, ads: list[Item]):
        """Добавляет несколько записей в таблицу viewed."""
        records = [(ad.id,) for ad in ads]

        with psycopg2.connect(self.connection_string) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO viewed (id) VALUES (%s) ON CONFLICT (id) DO NOTHING
                """,
                records,
            )
            conn.commit()

    def record_exists(self, record_id):
        """Проверяет, существует ли запись с заданным id."""
        with psycopg2.connect(self.connection_string) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM viewed WHERE id = %s",
                (record_id,),
            )
            return cursor.fetchone() is not None
