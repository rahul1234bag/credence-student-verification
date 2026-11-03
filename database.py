import sqlite3
import hashlib
from contextlib import contextmanager
from pathlib import Path


DATABASE_FILE = Path(__file__).with_name("credence.db")


@contextmanager
def get_database():
    """Database connection kholta hai aur kaam ke baad band karta hai."""
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def make_fingerprint(certificate_id, student_id, title, award_date):
    """Certificate details ka SHA-256 fingerprint banata hai."""
    details = f"{certificate_id}|{student_id}|{title}|{award_date}"
    return hashlib.sha256(details.encode()).hexdigest()


def setup_database():
    """Tables banata hai aur khaali database mein sample data daalta hai."""
    with get_database() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS students (
                student_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                programme TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS certificates (
                certificate_id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                title TEXT NOT NULL,
                award_date TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Valid',
                revoked_reason TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id)
                    REFERENCES students(student_id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS verification_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                certificate_id TEXT NOT NULL,
                result TEXT NOT NULL,
                checked_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        student_count = db.execute(
            "SELECT COUNT(*) FROM students"
        ).fetchone()[0]

        if student_count == 0:
            db.execute("""
                INSERT INTO students
                    (student_id, name, email, programme)
                VALUES (?, ?, ?, ?)
            """, (
                "STU-2026-001",
                "Aarav Mehta",
                "aarav@example.edu",
                "B.Sc. Computer Science"
            ))

            certificate_id = "CRD-7F2A91C4"
            student_id = "STU-2026-001"
            title = "Bachelor of Science in Computer Science"
            award_date = "2026-06-15"
            fingerprint = make_fingerprint(
                certificate_id,
                student_id,
                title,
                award_date
            )

            db.execute("""
                INSERT INTO certificates
                    (certificate_id, student_id, title,
                     award_date, fingerprint)
                VALUES (?, ?, ?, ?, ?)
            """, (
                certificate_id,
                student_id,
                title,
                award_date,
                fingerprint
            ))