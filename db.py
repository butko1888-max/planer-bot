import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

DATABASE_URL = os.environ["DATABASE_URL"]


@contextmanager
def conn():
    c = psycopg2.connect(DATABASE_URL, sslmode="require")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db():
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS planer_users (
                user_id BIGINT PRIMARY KEY,
                first_name TEXT,
                tz TEXT DEFAULT 'Europe/Kiev',
                evening_hour INT DEFAULT 22,
                morning_hour INT DEFAULT 8,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS planer_tasks (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                text TEXT NOT NULL,
                due_date DATE,
                remind_at TIMESTAMPTZ,
                reminded BOOLEAN DEFAULT FALSE,
                done BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_user_due ON planer_tasks(user_id, due_date, done);
            CREATE INDEX IF NOT EXISTS idx_tasks_remind ON planer_tasks(remind_at) WHERE reminded = FALSE AND done = FALSE;
        """)


def upsert_user(user_id: int, first_name: str):
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO planer_users (user_id, first_name)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET first_name = EXCLUDED.first_name
        """, (user_id, first_name))


def get_user(user_id: int):
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM planer_users WHERE user_id = %s", (user_id,))
        return cur.fetchone()


def all_users():
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM planer_users")
        return cur.fetchall()


def add_task(user_id: int, text: str, due_date=None, remind_at=None):
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            INSERT INTO planer_tasks (user_id, text, due_date, remind_at)
            VALUES (%s, %s, %s, %s)
            RETURNING *
        """, (user_id, text, due_date, remind_at))
        return cur.fetchone()


def tasks_for_date(user_id: int, date, include_done: bool = True):
    done_clause = "" if include_done else "AND done = FALSE"
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT * FROM planer_tasks
            WHERE user_id = %s AND due_date = %s {done_clause}
            ORDER BY done, remind_at NULLS LAST, id
        """, (user_id, date))
        return cur.fetchall()


def tasks_in_range(user_id: int, start_date, end_date, include_done: bool = True):
    done_clause = "" if include_done else "AND done = FALSE"
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT * FROM planer_tasks
            WHERE user_id = %s AND due_date BETWEEN %s AND %s {done_clause}
            ORDER BY due_date, done, remind_at NULLS LAST, id
        """, (user_id, start_date, end_date))
        return cur.fetchall()


def inbox_tasks(user_id: int):
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT * FROM planer_tasks
            WHERE user_id = %s AND due_date IS NULL AND done = FALSE
            ORDER BY id DESC
        """, (user_id,))
        return cur.fetchall()


def mark_done(task_id: int, user_id: int):
    with conn() as c, c.cursor() as cur:
        cur.execute("UPDATE planer_tasks SET done = TRUE WHERE id = %s AND user_id = %s", (task_id, user_id))


def mark_undone(task_id: int, user_id: int):
    with conn() as c, c.cursor() as cur:
        cur.execute("UPDATE planer_tasks SET done = FALSE WHERE id = %s AND user_id = %s", (task_id, user_id))


def delete_task(task_id: int, user_id: int):
    with conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM planer_tasks WHERE id = %s AND user_id = %s", (task_id, user_id))


def set_task_date(task_id: int, user_id: int, due_date, remind_at=None):
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            UPDATE planer_tasks SET due_date = %s, remind_at = %s, reminded = FALSE
            WHERE id = %s AND user_id = %s
        """, (due_date, remind_at, task_id, user_id))


def set_task_remind(task_id: int, user_id: int, remind_at):
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            UPDATE planer_tasks SET remind_at = %s, reminded = FALSE
            WHERE id = %s AND user_id = %s
        """, (remind_at, task_id, user_id))


def get_task(task_id: int, user_id: int):
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM planer_tasks WHERE id = %s AND user_id = %s", (task_id, user_id))
        return cur.fetchone()


def due_reminders():
    """Tasks whose remind_at <= now and not yet reminded."""
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT * FROM planer_tasks
            WHERE reminded = FALSE AND done = FALSE AND remind_at IS NOT NULL AND remind_at <= NOW()
        """)
        return cur.fetchall()


def mark_reminded(task_id: int):
    with conn() as c, c.cursor() as cur:
        cur.execute("UPDATE planer_tasks SET reminded = TRUE WHERE id = %s", (task_id,))
