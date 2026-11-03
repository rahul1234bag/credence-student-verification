import csv
import io
import secrets
import sqlite3
from pathlib import Path
from datetime import date

import qrcode
from flask import (
    Flask, Response, flash, jsonify, redirect,
    render_template, request, url_for
)

from database import get_database, make_fingerprint, setup_database


app = Flask(__name__)
app.secret_key = "credence-demo-key"

QR_FOLDER = Path(__file__).parent / "static" / "qr"
QR_FOLDER.mkdir(parents=True, exist_ok=True)


def update_old_database():
    """Purane demo database mein naye columns add karta hai."""
    with get_database() as db:
        columns = {
            column["name"]
            for column in db.execute(
                "PRAGMA table_info(certificates)"
            ).fetchall()
        }

        if "status" not in columns:
            db.execute("""
                ALTER TABLE certificates
                ADD COLUMN status TEXT NOT NULL DEFAULT 'Valid'
            """)

        if "revoked_reason" not in columns:
            db.execute("""
                ALTER TABLE certificates
                ADD COLUMN revoked_reason TEXT DEFAULT ''
            """)

        if "created_at" not in columns:
            # SQLite cannot add a column with CURRENT_TIMESTAMP as its default.
            db.execute("""
                ALTER TABLE certificates
                ADD COLUMN created_at TEXT
            """)
            db.execute("""
                UPDATE certificates
                SET created_at = CURRENT_TIMESTAMP
                WHERE created_at IS NULL
            """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS verification_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                certificate_id TEXT NOT NULL,
                result TEXT NOT NULL,
                checked_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


@app.route("/")
def home():
    """Dashboard, search results aur verification history dikhata hai."""
    search_text = request.args.get("q", "").strip()

    with get_database() as db:
        students = db.execute("""
            SELECT student_id, name, email, programme
            FROM students
            WHERE name LIKE ? OR student_id LIKE ? OR programme LIKE ?
            ORDER BY name
        """, (
            f"%{search_text}%",
            f"%{search_text}%",
            f"%{search_text}%"
        )).fetchall()

        certificates = db.execute("""
            SELECT certificates.*,
                   students.name AS student_name
            FROM certificates
            JOIN students
              ON students.student_id = certificates.student_id
            WHERE certificates.title LIKE ?
               OR certificates.certificate_id LIKE ?
               OR students.name LIKE ?
            ORDER BY certificates.created_at DESC
        """, (
            f"%{search_text}%",
            f"%{search_text}%",
            f"%{search_text}%"
        )).fetchall()

        verification_logs = db.execute("""
            SELECT certificate_id, result, checked_at
            FROM verification_logs
            ORDER BY log_id DESC
            LIMIT 10
        """).fetchall()

    return render_template(
        "index.html",
        students=students,
        certificates=certificates,
        verification_logs=verification_logs,
        search_text=search_text,
        today=date.today().isoformat()
    )


@app.route("/students/add", methods=["POST"])
def add_student():
    student_id = request.form.get("student_id", "").strip().upper()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    programme = request.form.get("programme", "").strip()

    if not student_id or not name or not email or not programme:
        flash("Student ki saari details bharo.", "error")
        return redirect(url_for("home"))

    try:
        with get_database() as db:
            db.execute("""
                INSERT INTO students
                    (student_id, name, email, programme)
                VALUES (?, ?, ?, ?)
            """, (student_id, name, email, programme))

        flash("Student record save ho gaya.", "success")

    except sqlite3.IntegrityError:
        flash("Yeh student ID pehle se registered hai.", "error")

    return redirect(url_for("home"))


@app.route("/certificates/issue", methods=["POST"])
def issue_certificate():
    student_id = request.form.get("student_id", "").strip()
    title = request.form.get("title", "").strip()
    award_date = request.form.get("award_date", "").strip()

    if not student_id or not title or not award_date:
        flash("Student, certificate title aur date bharo.", "error")
        return redirect(url_for("home"))

    certificate_id = "CRD-" + secrets.token_hex(4).upper()
    fingerprint = make_fingerprint(
        certificate_id,
        student_id,
        title,
        award_date
    )

    try:
        with get_database() as db:
            db.execute("""
                INSERT INTO certificates
                    (certificate_id, student_id, title,
                     award_date, fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                certificate_id,
                student_id,
                title,
                award_date,
                fingerprint,
                date.today().isoformat()
            ))

        # QR image certificate ID encode karti hai.
        qr_image = qrcode.make(certificate_id)
        qr_image.save(QR_FOLDER / f"{certificate_id}.png")

        flash(
            f"Certificate issue ho gaya. ID: {certificate_id}",
            "success"
        )

    except sqlite3.IntegrityError:
        flash("Student record nahi mila. Pehle student add karo.", "error")

    return redirect(url_for("home"))


@app.route("/certificates/verify", methods=["POST"])
def verify_certificate():
    certificate_id = request.form.get(
        "certificate_id", ""
    ).strip().upper()

    with get_database() as db:
        certificate = db.execute("""
            SELECT certificates.*,
                   students.name AS student_name
            FROM certificates
            JOIN students
              ON students.student_id = certificates.student_id
            WHERE certificates.certificate_id = ?
        """, (certificate_id,)).fetchone()

        if certificate is None:
            result = "Not found"
            db.execute("""
                INSERT INTO verification_logs (certificate_id, result)
                VALUES (?, ?)
            """, (certificate_id, result))

            flash("Is ID ka certificate nahi mila.", "error")
            return redirect(url_for("home"))

        current_fingerprint = make_fingerprint(
            certificate["certificate_id"],
            certificate["student_id"],
            certificate["title"],
            certificate["award_date"]
        )

        if current_fingerprint != certificate["fingerprint"]:
            result = "Fingerprint mismatch"
            flash("Certificate fingerprint match nahi hua.", "error")

        elif certificate["status"] == "Revoked":
            result = "Revoked"
            flash(
                f"Yeh certificate revoke ho chuka hai. "
                f"Reason: {certificate['revoked_reason']}",
                "error"
            )

        else:
            result = "Valid"
            flash(
                f"Verified! {certificate['student_name']} — "
                f"{certificate['title']}",
                "success"
            )

        db.execute("""
            INSERT INTO verification_logs (certificate_id, result)
            VALUES (?, ?)
        """, (certificate_id, result))

    return redirect(url_for("home"))


@app.route("/certificates/revoke", methods=["POST"])
def revoke_certificate():
    certificate_id = request.form.get(
        "certificate_id", ""
    ).strip().upper()
    reason = request.form.get("reason", "").strip()

    if not reason:
        flash("Revoke karne ka reason likho.", "error")
        return redirect(url_for("home"))

    with get_database() as db:
        certificate = db.execute("""
            SELECT status FROM certificates
            WHERE certificate_id = ?
        """, (certificate_id,)).fetchone()

        if certificate is None:
            flash("Certificate nahi mila.", "error")
        elif certificate["status"] == "Revoked":
            flash("Yeh certificate pehle hi revoke ho chuka hai.", "error")
        else:
            db.execute("""
                UPDATE certificates
                SET status = 'Revoked', revoked_reason = ?
                WHERE certificate_id = ?
            """, (reason, certificate_id))
            flash("Certificate revoke kar diya gaya.", "success")

    return redirect(url_for("home"))


@app.route("/certificates.csv")
def export_certificates():
    """Certificates ki CSV file download karata hai."""
    with get_database() as db:
        certificates = db.execute("""
            SELECT certificates.certificate_id,
                   students.name,
                   students.student_id,
                   certificates.title,
                   certificates.award_date,
                   certificates.status
            FROM certificates
            JOIN students
              ON students.student_id = certificates.student_id
            ORDER BY certificates.created_at DESC
        """).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Certificate ID",
        "Student name",
        "Student ID",
        "Certificate title",
        "Award date",
        "Status"
    ])

    for certificate in certificates:
        writer.writerow([
            certificate["certificate_id"],
            certificate["name"],
            certificate["student_id"],
            certificate["title"],
            certificate["award_date"],
            certificate["status"]
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=credence-certificates.csv"
        }
    )


@app.route("/api/verify/<certificate_id>")
def verify_api(certificate_id):
    """QR scan ya API demo ke liye JSON verification response deta hai."""
    certificate_id = certificate_id.strip().upper()

    with get_database() as db:
        certificate = db.execute("""
            SELECT certificates.*,
                   students.name AS student_name
            FROM certificates
            JOIN students
              ON students.student_id = certificates.student_id
            WHERE certificates.certificate_id = ?
        """, (certificate_id,)).fetchone()

    if certificate is None:
        return jsonify({
            "found": False,
            "valid": False,
            "message": "Certificate not found"
        }), 404

    current_fingerprint = make_fingerprint(
        certificate["certificate_id"],
        certificate["student_id"],
        certificate["title"],
        certificate["award_date"]
    )

    valid = (
        current_fingerprint == certificate["fingerprint"]
        and certificate["status"] == "Valid"
    )

    return jsonify({
        "found": True,
        "valid": valid,
        "certificate_id": certificate["certificate_id"],
        "student": certificate["student_name"],
        "title": certificate["title"],
        "award_date": certificate["award_date"],
        "status": certificate["status"],
        "fingerprint": certificate["fingerprint"]
    })


# Run database setup locally and when Gunicorn imports this module.
setup_database()
update_old_database()

if __name__ == "__main__":
    app.run(debug=False)