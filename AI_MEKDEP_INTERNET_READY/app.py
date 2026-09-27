# -*- coding: utf-8 -*-
from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, send_file
import json
import os
import re
import sqlite3
from pathlib import Path
from collections import Counter
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from database import initialize_database, DB_NAME

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

app = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AI_MEKDEP_DATA_DIR", str(BASE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
BOOK_UPLOAD_DIR = DATA_DIR / "uploads" / "books"
BOOK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GB / book
ALLOWED_BOOK_EXTENSIONS = {"pdf", "docx", "txt", "rtf"}
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "ai-mekdep-local-2026")

# Mekdep okuwçysy üçin çykdajysy pes modeli başlangyç hökmünde ulanýarys.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")


def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


initialize_database()


@app.route("/health")
def health():
    return {"status": "ok", "service": "AI MEKDEP"}, 200


def allowed_book_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_BOOK_EXTENSIONS


def split_text(text, max_chars=2600):
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            cut = text.rfind(". ", start, end)
            if cut > start + max_chars // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        start = end
    return [x for x in chunks if x]


def extract_book_text(file_path, ext):
    pages = []
    ext = ext.lower()
    if ext == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(file_path))
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            for part in split_text(text):
                pages.append((i, part))
    elif ext == "docx":
        from docx import Document
        doc = Document(str(file_path))
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        for part in split_text(text):
            pages.append((None, part))
    else:
        raw = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        if ext == "rtf":
            raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", raw)
            raw = raw.replace("{", " ").replace("}", " ")
        for part in split_text(raw):
            pages.append((None, part))
    return pages


def subject_id_for(name):
    conn = get_db_connection()
    row = conn.execute("SELECT id FROM subjects WHERE name=?", (name,)).fetchone()
    conn.close()
    return row["id"] if row else None


def list_books(class_number=None, subject=None, q=None):
    conn = get_db_connection()
    sql = """
        SELECT b.*, s.name AS subject
        FROM books b JOIN subjects s ON s.id=b.subject_id
        WHERE 1=1
    """
    params = []
    if class_number:
        sql += " AND b.class_number=?"
        params.append(class_number)
    if subject:
        sql += " AND s.name=?"
        params.append(subject)
    if q:
        sql += " AND (lower(b.title) LIKE lower(?) OR lower(COALESCE(b.author,'')) LIKE lower(?))"
        params.extend([f"%{q}%", f"%{q}%"])
    sql += " ORDER BY b.class_number, s.name, b.title"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def tokenize(text):
    words = re.findall(r"[A-Za-zÄäÖöÜüÇçŞşŽžÝýŇňĞğ\u0400-\u04FF0-9]+", (text or "").lower())
    stop = {"we", "hem", "barada", "üçin", "bilen", "bu", "şu", "bir", "the", "and", "of", "to", "in"}
    return [w for w in words if len(w) > 2 and w not in stop]


def relevant_book_chunks(topic, limit=6):
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT bc.id, bc.book_id, bc.page_number, bc.chunk_order, bc.text,
               b.title, b.author, b.year
        FROM book_chunks bc
        JOIN books b ON b.id=bc.book_id
        JOIN subjects s ON s.id=b.subject_id
        WHERE b.class_number=? AND s.name=?
        """,
        (topic["class_number"], topic["subject"]),
    ).fetchall()
    conn.close()
    if not rows:
        return []
    terms = tokenize(f"{topic['section']} {topic['topic']}")
    counts = Counter(terms)
    scored = []
    for row in rows:
        low = row["text"].lower()
        score = sum(low.count(term) * weight for term, weight in counts.items())
        if score:
            scored.append((score, row))
    scored.sort(key=lambda x: (-x[0], x[1]["book_id"], x[1]["chunk_order"]))
    if not scored:
        return rows[:limit]
    return [row for _, row in scored[:limit]]


def book_context_for_topic(topic, limit=6):
    chunks = relevant_book_chunks(topic, limit=limit)
    if not chunks:
        return "", []
    parts = []
    sources = []
    for c in chunks:
        page = f", sah. {c['page_number']}" if c["page_number"] else ""
        parts.append(f"[Kitap: {c['title']}{page}]\n{c['text']}")
        sources.append({"book_id": c["book_id"], "title": c["title"], "page_number": c["page_number"]})
    return "\n\n".join(parts), sources


def get_openai_client():
    if OpenAI is None:
        return None, "OpenAI Python paketi gurnalmady. start_ai_mekdep.bat faýlyny işlediň."
    if not os.environ.get("OPENAI_API_KEY"):
        return None, "OPENAI_API_KEY tapylmady. AI funksiýasy üçin OpenAI API açaryny giriziň we programmany täzeden açyň."
    try:
        return OpenAI(), None
    except Exception as exc:
        return None, f"OpenAI birikmesini taýýarlap bolmady: {exc}"


def call_openai(instructions, user_input):
    client, error = get_openai_client()
    if error:
        return None, error
    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            instructions=instructions,
            input=user_input,
        )
        return response.output_text.strip(), None
    except Exception as exc:
        return None, f"OpenAI jogaby alnyp bilinmedi: {exc}"


def get_topic(topic_id):
    conn = get_db_connection()
    topic = conn.execute(
        """
        SELECT topics.id, topics.class_number, topics.section,
               topics.topic, topics.hours, topics.explanation,
               topics.example_text, topics.practical_task,
               topics.source_year, subjects.name AS subject
        FROM topics
        JOIN subjects ON topics.subject_id = subjects.id
        WHERE topics.id = ?
        """,
        (topic_id,),
    ).fetchone()
    conn.close()
    return topic


def curriculum_context(topic):
    parts = [
        f"Synp: {topic['class_number']}",
        f"Ders: {topic['subject']}",
        f"Bölüm: {topic['section']}",
        f"Tema: {topic['topic']}",
    ]
    if topic["hours"]:
        parts.append(f"Maksatnamadaky sagat: {topic['hours']}")
    if topic["source_year"]:
        parts.append(f"Maksatnama ýyly: {topic['source_year']}")
    if topic["explanation"]:
        parts.append(f"Bazadaky düşündiriş: {topic['explanation']}")
    if topic["example_text"]:
        parts.append(f"Bazadaky mysal: {topic['example_text']}")
    if topic["practical_task"]:
        parts.append(f"Bazadaky amaly iş: {topic['practical_task']}")
    return "\n".join(parts)


def get_cached_lesson(topic_id):
    conn = get_db_connection()
    row = conn.execute(
        "SELECT lesson_text, model, generated_at FROM ai_content WHERE topic_id = ?",
        (topic_id,),
    ).fetchone()
    conn.close()
    return row


def save_cached_lesson(topic_id, lesson_text):
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO ai_content (topic_id, lesson_text, model, generated_at)
        VALUES (?, ?, ?, datetime('now','localtime'))
        ON CONFLICT(topic_id) DO UPDATE SET
            lesson_text = excluded.lesson_text,
            model = excluded.model,
            generated_at = excluded.generated_at
        """,
        (topic_id, lesson_text, OPENAI_MODEL),
    )
    conn.commit()
    conn.close()


def generate_topic_lesson(topic, force=False):
    if not force:
        cached = get_cached_lesson(topic["id"])
        if cached and cached["lesson_text"]:
            return cached["lesson_text"], None, True

    instructions = """Sen AI MEKDEP atly mekdep okuw platformasynyň okuw kömekçisi.
Okuwça ýaşyna we synp derejesine laýyk, düşnükli, takyk we okuw maksatly jogap ber.
Esasy dil türkmen dili bolsun. Rus dili ýa-da Iňlis dili dersinde gerek ýerinde şol diliň mysallaryny ulan.
Berlen tema çäginden çykma. Tema okuw maksatnamasyna laýyk düşündirilsin.
Jogapda: Giriş, Esasy düşünjeler, Ädimme-ädim düşündiriş, Mysallar, Ýatda sakla, Özüňi barla bölümlerini ulan.
Matematika, fizika, himiýa ýaly derslerde formulalary we hasaplamalary ädimme-ädim düşündir.
"""
    book_context, _book_sources = book_context_for_topic(topic)
    user_input = f"""Şu tema boýunça okuwçy sahypada tema basan badyna okap biljek giňişleýin sapak düşündirişini taýýarla.

{curriculum_context(topic)}

Elektron kitaphanadan tema degişli bölekler:
{book_context or 'Degişli kitap bölegi tapylmady.'}

Talaplar:
- Synp derejesine laýyk bolsun.
- Zerur terminleri aýratyn düşündir.
- 2-3 ýönekeý mysal goş.
- Ahyrynda 3 sany özüňi barla soragyny goş.
"""
    answer, error = call_openai(instructions, user_input)
    if answer:
        save_cached_lesson(topic["id"], answer)
        return answer, None, False
    return None, error, False


def get_subjects_by_class():
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT sc.class_number, s.name, s.icon, sc.total_hours, sc.track, sc.source_year
        FROM subject_classes sc
        JOIN subjects s ON s.id = sc.subject_id
        ORDER BY sc.class_number, s.name
        """
    ).fetchall()
    conn.close()
    data = {str(g): [] for g in range(5, 13)}
    for row in rows:
        data[str(row["class_number"])].append(
            {
                "name": row["name"],
                "icon": row["icon"] or "📘",
                "total_hours": row["total_hours"],
                "track": row["track"],
                "source_year": row["source_year"],
            }
        )
    return data


def all_topic_choices():
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT t.id, t.class_number, s.name AS subject, t.section, t.topic,
               (SELECT COUNT(*) FROM tests x WHERE x.topic_id=t.id) AS test_count
        FROM topics t JOIN subjects s ON s.id=t.subject_id
        ORDER BY t.class_number, s.name, t.topic_order
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def teacher_required():
    return bool(session.get("teacher_id"))


def current_teacher():
    teacher_id = session.get("teacher_id")
    if not teacher_id:
        return None
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM teachers WHERE id=? AND is_active=1", (teacher_id,)).fetchone()
    conn.close()
    return row


def teacher_scope(teacher_id):
    conn = get_db_connection()
    subjects = conn.execute(
        """SELECT DISTINCT s.id,s.name,s.icon FROM teacher_courses tc
           JOIN subjects s ON s.id=tc.subject_id WHERE tc.teacher_id=? ORDER BY s.name""",
        (teacher_id,),
    ).fetchall()
    classes = [r[0] for r in conn.execute(
        "SELECT DISTINCT class_number FROM teacher_courses WHERE teacher_id=? ORDER BY class_number",
        (teacher_id,),
    ).fetchall()]
    conn.close()
    return subjects, classes


def teacher_can_use_course(teacher_id, subject_id, class_number):
    conn = get_db_connection()
    ok = conn.execute(
        "SELECT 1 FROM teacher_courses WHERE teacher_id=? AND subject_id=? AND class_number=?",
        (teacher_id, subject_id, class_number),
    ).fetchone()
    conn.close()
    return bool(ok)


def teacher_topic_choices(teacher_id):
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT t.id,t.class_number,s.name AS subject,t.section,t.topic
           FROM topics t
           JOIN subjects s ON s.id=t.subject_id
           JOIN teacher_courses tc ON tc.subject_id=s.id AND tc.class_number=t.class_number AND tc.teacher_id=?
           ORDER BY t.class_number,s.name,t.topic_order""",
        (teacher_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)




def admin_required():
    admin_id = session.get("admin_id")
    if not admin_id:
        return False
    conn = get_db_connection()
    row = conn.execute("SELECT id FROM admins WHERE id=? AND is_active=1", (admin_id,)).fetchone()
    conn.close()
    return bool(row)


def current_admin():
    admin_id = session.get("admin_id")
    if not admin_id:
        return None
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM admins WHERE id=? AND is_active=1", (admin_id,)).fetchone()
    conn.close()
    return row


def log_admin_action(action, details=""):
    admin_id = session.get("admin_id")
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO admin_logs(admin_id,action,details) VALUES(?,?,?)",
        (admin_id, action, details),
    )
    conn.commit()
    conn.close()


def admin_course_options():
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT sc.class_number,s.id AS subject_id,s.name,s.icon
           FROM subject_classes sc JOIN subjects s ON s.id=sc.subject_id
           ORDER BY sc.class_number,s.name"""
    ).fetchall()
    conn.close()
    by_class = {str(g): [] for g in range(5, 13)}
    for r in rows:
        by_class[str(r["class_number"])].append({
            "subject_id": r["subject_id"], "name": r["name"], "icon": r["icon"] or "📘"
        })
    return by_class


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/student")
def student():
    return render_template("student.html", subjects_by_class=get_subjects_by_class())


@app.route("/courses")
def courses():
    return render_template("courses.html", subjects_by_class=get_subjects_by_class())


@app.route("/topics")
def topics():
    class_number = request.args.get("class")
    subject = request.args.get("subject")
    topics_list = []
    course = None
    if class_number and subject:
        conn = get_db_connection()
        course = conn.execute(
            """
            SELECT sc.total_hours, sc.track, sc.source_year, s.icon
            FROM subject_classes sc
            JOIN subjects s ON s.id = sc.subject_id
            WHERE s.name = ? AND sc.class_number = ?
            """,
            (subject, class_number),
        ).fetchone()
        topics_list = conn.execute(
            """
            SELECT topics.id, topics.section, topics.topic,
                   topics.hours, topics.topic_order, topics.source_year
            FROM topics
            JOIN subjects ON topics.subject_id = subjects.id
            WHERE subjects.name = ? AND topics.class_number = ?
            ORDER BY topics.topic_order
            """,
            (subject, class_number),
        ).fetchall()
        teacher_tests = conn.execute(
            """SELECT tt.id,tt.title,tt.description,tr.full_name AS teacher_name,
                      (SELECT COUNT(*) FROM teacher_test_questions q WHERE q.test_set_id=tt.id) AS question_count
               FROM teacher_test_sets tt
               JOIN subjects s ON s.id=tt.subject_id
               JOIN teachers tr ON tr.id=tt.teacher_id
               WHERE tt.is_published=1 AND tr.is_active=1 AND s.name=? AND tt.class_number=?
               ORDER BY tt.id DESC""",
            (subject, class_number),
        ).fetchall()
        conn.close()
    else:
        teacher_tests = []
    return render_template(
        "topics.html",
        class_number=class_number,
        subject=subject,
        topics=topics_list,
        course=course,
        teacher_tests=teacher_tests,
    )


@app.route("/study")
def study():
    topic_id = request.args.get("topic")
    if not topic_id:
        return "Tema saýlanmady.", 400
    topic = get_topic(topic_id)
    if topic is None:
        return "Tema tapylmady.", 404
    lesson_text, lesson_error, was_cached = generate_topic_lesson(topic)
    _book_context, book_sources = book_context_for_topic(topic)
    return render_template(
        "study.html",
        topic=topic,
        lesson_text=lesson_text,
        lesson_error=lesson_error,
        lesson_cached=was_cached,
        ai_answer=None,
        last_question="",
        book_sources=book_sources,
        practical_ai=None,
    )


@app.route("/regenerate-lesson", methods=["POST"])
def regenerate_lesson():
    topic_id = request.form.get("topic_id")
    topic = get_topic(topic_id)
    if topic is None:
        return "Tema tapylmady.", 404
    lesson_text, lesson_error, _ = generate_topic_lesson(topic, force=True)
    _book_context, book_sources = book_context_for_topic(topic)
    return render_template(
        "study.html",
        topic=topic,
        lesson_text=lesson_text,
        lesson_error=lesson_error,
        lesson_cached=False,
        ai_answer=None,
        last_question="",
        book_sources=book_sources,
        practical_ai=None,
    )


@app.route("/ask-ai", methods=["POST"])
def ask_ai():
    topic_id = request.form.get("topic_id")
    question = (request.form.get("question") or "").strip()
    topic = get_topic(topic_id)
    if topic is None:
        return "Tema tapylmady.", 404
    cached = get_cached_lesson(topic["id"])
    lesson_text = cached["lesson_text"] if cached else None
    if not question:
        ai_answer = "Soragyňy ýazyp, täzeden iber."
    else:
        instructions = """Sen AI MEKDEP okuw kömekçisi.
Okuwçynyň häzirki saýlanan temasy boýunça soragyna jogap ber.
Jogaby okuwçynyň synp derejesine laýyk, ýönekeý, takyk we sabyrly düşündir.
Okuwçy mysalyň işlenşini sorasa, diňe netijäni aýtma: her ädimiň sebäbini düşündir.
Matematika/fizika/himiýa meselelerinde berlenleri, formulany, ýerine goýmagy, hasaplamany we jogaby aýratyn görkez.
Programmirlemede kody düşündir we möhüm setirleriň näme edýändigini aýt.
"""
        book_context, _book_sources = book_context_for_topic(topic)
        user_input = f"""Häzirki okuw konteksti:
{curriculum_context(topic)}

Elektron kitaphanadan degişli maglumat:
{book_context or 'Degişli kitap bölegi tapylmady.'}

AI tarapyndan taýýarlanan sapak düşündirişi:
{lesson_text or 'Entäk döredilmedi.'}

Okuwçynyň soragy:
{question}

Kitap maglumatlary bar bolsa, jogaby şolara daýandyryp ber. Şu soraga göni jogap ber."""
        ai_answer, api_error = call_openai(instructions, user_input)
        if api_error:
            ai_answer = api_error
    _book_context, book_sources = book_context_for_topic(topic)
    return render_template(
        "study.html",
        topic=topic,
        lesson_text=lesson_text,
        lesson_error=None,
        lesson_cached=bool(cached),
        ai_answer=ai_answer,
        last_question=question,
        book_sources=book_sources,
        practical_ai=None,
    )


@app.route("/ai", methods=["GET", "POST"])
def ai_center():
    topics_data = all_topic_choices()
    answer = None
    error = None
    selected_topic = None
    question = ""
    if request.method == "POST":
        topic_id = request.form.get("topic_id")
        question = (request.form.get("question") or "").strip()
        selected_topic = get_topic(topic_id) if topic_id else None
        if not selected_topic:
            error = "Ilki tema saýlaň."
        elif not question:
            error = "Soragyňyzy ýazyň."
        else:
            instructions = """Sen AI MEKDEP okuw kömekçisi. Saýlanan synp, ders we tema boýunça okuwça düşnükli jogap ber.
Jogap okuw maksatly, ýaş derejesine laýyk we ädimme-ädim bolsun. Mysal soralsa, işlenşini düşündir."""
            answer, error = call_openai(instructions, curriculum_context(selected_topic) + "\n\nSorag: " + question)
    return render_template(
        "ai_center.html",
        topics_data=topics_data,
        answer=answer,
        error=error,
        selected_topic=selected_topic,
        question=question,
    )


@app.route("/tests")
def tests_center():
    class_number = request.args.get("class", "")
    subject = request.args.get("subject", "")
    conn = get_db_connection()
    query = """
        SELECT t.id, t.class_number, s.name AS subject, t.section, t.topic,
               COUNT(x.id) AS test_count
        FROM topics t
        JOIN subjects s ON s.id=t.subject_id
        LEFT JOIN tests x ON x.topic_id=t.id
        WHERE 1=1
    """
    params = []
    if class_number:
        query += " AND t.class_number=?"
        params.append(class_number)
    if subject:
        query += " AND s.name=?"
        params.append(subject)
    query += " GROUP BY t.id ORDER BY t.class_number, s.name, t.topic_order LIMIT 800"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return render_template(
        "tests_center.html",
        rows=rows,
        subjects_by_class=get_subjects_by_class(),
        selected_class=class_number,
        selected_subject=subject,
    )


@app.route("/generate-test", methods=["POST"])
def generate_test():
    topic_id = request.form.get("topic_id")
    topic = get_topic(topic_id)
    if not topic:
        return "Tema tapylmady.", 404
    instructions = """Sen AI MEKDEP üçin test taýýarlaýarsyň. Diňe JSON array çykart.
Her elementde question, option_a, option_b, option_c, option_d, correct_answer meýdanlary bolsun.
correct_answer diňe A, B, C ýa-da D bolsun. 5 sany synp derejesine laýyk, düşnükli test taýýarla."""
    raw, error = call_openai(instructions, curriculum_context(topic))
    if error:
        return redirect(url_for("test", topic=topic_id, msg=error))
    try:
        items = extract_json(raw)
        conn = get_db_connection()
        conn.execute("DELETE FROM tests WHERE topic_id=?", (topic_id,))
        for item in items[:5]:
            correct = str(item.get("correct_answer", "")).strip().upper()
            if correct not in {"A", "B", "C", "D"}:
                continue
            conn.execute(
                """INSERT INTO tests(topic_id,question,option_a,option_b,option_c,option_d,correct_answer)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    topic_id,
                    item.get("question", ""),
                    item.get("option_a", ""),
                    item.get("option_b", ""),
                    item.get("option_c", ""),
                    item.get("option_d", ""),
                    correct,
                ),
            )
        conn.commit()
        conn.close()
        return redirect(url_for("test", topic=topic_id))
    except Exception as exc:
        return redirect(url_for("test", topic=topic_id, msg=f"Test maglumatyny okap bolmady: {exc}"))


@app.route("/test", methods=["GET", "POST"])
def test():
    topic_id = request.args.get("topic") or request.form.get("topic_id")
    if not topic_id:
        return "Tema saýlanmady.", 400
    topic = get_topic(topic_id)
    if topic is None:
        return "Tema tapylmady.", 404
    conn = get_db_connection()
    tests = conn.execute(
        """
        SELECT id, question, option_a, option_b, option_c, option_d, correct_answer
        FROM tests WHERE topic_id = ? ORDER BY id
        """,
        (topic_id,),
    ).fetchall()
    result = None
    details = []
    student_name = (request.form.get("student_name") or "").strip()
    if request.method == "POST" and tests:
        score = 0
        for item in tests:
            selected = request.form.get(f"q_{item['id']}")
            correct = item["correct_answer"]
            is_correct = selected == correct
            if is_correct:
                score += 1
            details.append(
                {
                    "question": item["question"],
                    "selected": selected,
                    "correct": correct,
                    "is_correct": is_correct,
                }
            )
        percent = round(score * 100 / len(tests))
        result = {"score": score, "total": len(tests), "percent": percent}
        if student_name:
            conn.execute(
                """INSERT INTO test_results(student_name,class_number,subject,topic_id,topic_name,score,total,percent)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    student_name,
                    topic["class_number"],
                    topic["subject"],
                    topic["id"],
                    topic["topic"],
                    score,
                    len(tests),
                    percent,
                ),
            )
            conn.commit()
    conn.close()
    return render_template(
        "test.html",
        topic=topic,
        tests=tests,
        result=result,
        details=details,
        student_name=student_name,
        msg=request.args.get("msg"),
    )


@app.route("/results")
def results():
    name = (request.args.get("name") or "").strip()
    rows = []
    summary = None
    if name:
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT * FROM test_results WHERE lower(student_name)=lower(?) ORDER BY id DESC""",
            (name,),
        ).fetchall()
        if rows:
            avg = round(sum(r["percent"] for r in rows) / len(rows))
            summary = {"attempts": len(rows), "average": avg, "best": max(r["percent"] for r in rows)}
        conn.close()
    return render_template("results.html", name=name, rows=rows, summary=summary)


@app.route("/teacher", methods=["GET", "POST"])
def teacher():
    if teacher_required():
        return redirect(url_for("teacher_dashboard"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM teachers WHERE username=? COLLATE NOCASE AND is_active=1",
            (username,),
        ).fetchone()
        conn.close()
        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session["teacher_id"] = row["id"]
            session["teacher_name"] = row["full_name"]
            return redirect(url_for("teacher_dashboard"))
        error = "Login ýa-da parol nädogry."
    return render_template("teacher_login.html", error=error)


@app.route("/teacher/register", methods=["GET", "POST"])
def teacher_register():
    if teacher_required():
        return redirect(url_for("teacher_dashboard"))
    conn = get_db_connection()
    course_rows = conn.execute(
        """SELECT sc.class_number,s.id,s.name,s.icon FROM subject_classes sc
           JOIN subjects s ON s.id=sc.subject_id ORDER BY sc.class_number,s.name"""
    ).fetchall()
    conn.close()
    courses_by_class = {str(g): [] for g in range(5,13)}
    for r in course_rows:
        courses_by_class[str(r["class_number"])].append({"id": r["id"], "name": r["name"], "icon": r["icon"] or "📘"})
    error = None
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        selected_courses = []
        for value in request.form.getlist("courses"):
            try:
                grade_s, sid_s = value.split(":", 1)
                grade, sid = int(grade_s), int(sid_s)
                if 5 <= grade <= 12:
                    selected_courses.append((grade, sid))
            except Exception:
                pass
        if len(full_name) < 3:
            error = "Adyňyzy we familiýaňyzy doly ýazyň."
        elif len(username) < 3:
            error = "Login azyndan 3 belgiden ybarat bolmaly."
        elif len(password) < 6:
            error = "Parol azyndan 6 belgiden ybarat bolmaly."
        elif password != confirm:
            error = "Parollar gabat gelenok."
        elif not selected_courses:
            error = "Azyndan bir synp-ders kombinasiýasyny saýlaň."
        else:
            conn = get_db_connection()
            try:
                cur = conn.execute(
                    "INSERT INTO teachers(full_name,username,password_hash) VALUES(?,?,?)",
                    (full_name, username, generate_password_hash(password)),
                )
                teacher_id = cur.lastrowid
                for grade, sid in sorted(set(selected_courses)):
                    valid = conn.execute("SELECT 1 FROM subject_classes WHERE subject_id=? AND class_number=?", (sid, grade)).fetchone()
                    if valid:
                        conn.execute("INSERT OR IGNORE INTO teacher_courses(teacher_id,subject_id,class_number) VALUES(?,?,?)", (teacher_id, sid, grade))
                conn.commit()
                session.clear()
                session["teacher_id"] = teacher_id
                session["teacher_name"] = full_name
                conn.close()
                return redirect(url_for("teacher_dashboard"))
            except sqlite3.IntegrityError:
                conn.rollback()
                conn.close()
                error = "Bu login eýýäm ulanylýar. Başga login saýlaň."
    return render_template("teacher_register.html", courses_by_class=courses_by_class, error=error)


@app.route("/teacher/logout")
def teacher_logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/teacher/dashboard")
def teacher_dashboard():
    if not teacher_required():
        return redirect(url_for("teacher"))
    teacher_row = current_teacher()
    if not teacher_row:
        session.clear()
        return redirect(url_for("teacher"))
    subject_rows, class_numbers = teacher_scope(teacher_row["id"])
    conn = get_db_connection()
    own_tests = conn.execute("SELECT COUNT(*) FROM teacher_test_sets WHERE teacher_id=?", (teacher_row["id"],)).fetchone()[0]
    own_questions = conn.execute(
        """SELECT COUNT(*) FROM teacher_test_questions q JOIN teacher_test_sets t ON t.id=q.test_set_id WHERE t.teacher_id=?""",
        (teacher_row["id"],),
    ).fetchone()[0]
    own_results = conn.execute(
        """SELECT COUNT(*) FROM teacher_test_results r JOIN teacher_test_sets t ON t.id=r.test_set_id WHERE t.teacher_id=?""",
        (teacher_row["id"],),
    ).fetchone()[0]
    own_books = conn.execute("SELECT COUNT(*) FROM books WHERE uploaded_by=?", (teacher_row["username"],)).fetchone()[0]
    recent = conn.execute(
        """SELECT r.*,tt.title,s.name AS subject,tt.class_number
           FROM teacher_test_results r JOIN teacher_test_sets tt ON tt.id=r.test_set_id
           JOIN subjects s ON s.id=tt.subject_id
           WHERE tt.teacher_id=? ORDER BY r.id DESC LIMIT 20""",
        (teacher_row["id"],),
    ).fetchall()
    conn.close()
    stats = {
        "subjects": len(subject_rows),
        "classes": len(class_numbers),
        "tests": own_tests,
        "questions": own_questions,
        "results": own_results,
        "books": own_books,
    }
    return render_template(
        "teacher_dashboard.html",
        teacher=teacher_row,
        stats=stats,
        recent=recent,
        subjects=subject_rows,
        classes=class_numbers,
        topics_data=teacher_topic_choices(teacher_row["id"]),
    )


@app.route("/teacher/topic", methods=["GET", "POST"])
def teacher_topic():
    if not teacher_required():
        return redirect(url_for("teacher"))
    teacher_row = current_teacher()
    topic_id = request.values.get("topic_id")
    topic = get_topic(topic_id) if topic_id else None
    saved = False
    if topic:
        sid = subject_id_for(topic["subject"])
        if not teacher_can_use_course(teacher_row["id"], sid, int(topic["class_number"])):
            return "Bu temany redaktirlemäge rugsat ýok.", 403
    if request.method == "POST" and topic:
        conn = get_db_connection()
        conn.execute(
            "UPDATE topics SET explanation=?, example_text=?, practical_task=? WHERE id=?",
            (request.form.get("explanation", ""), request.form.get("example_text", ""), request.form.get("practical_task", ""), topic_id),
        )
        conn.execute("DELETE FROM ai_content WHERE topic_id=?", (topic_id,))
        conn.commit()
        conn.close()
        topic = get_topic(topic_id)
        saved = True
    return render_template(
        "teacher_topic.html",
        topic=topic,
        topics_data=teacher_topic_choices(teacher_row["id"]),
        saved=saved,
    )


@app.route("/teacher/tests")
def teacher_tests_manage():
    if not teacher_required():
        return redirect(url_for("teacher"))
    teacher_row = current_teacher()
    conn = get_db_connection()
    courses = conn.execute(
        """SELECT tc.class_number,s.id AS subject_id,s.name AS subject,s.icon
           FROM teacher_courses tc JOIN subjects s ON s.id=tc.subject_id
           WHERE tc.teacher_id=? ORDER BY tc.class_number,s.name""", (teacher_row["id"],)
    ).fetchall()
    tests = conn.execute(
        """SELECT tt.*,s.name AS subject,
                  (SELECT COUNT(*) FROM teacher_test_questions q WHERE q.test_set_id=tt.id) AS question_count,
                  (SELECT COUNT(*) FROM teacher_test_results r WHERE r.test_set_id=tt.id) AS result_count
           FROM teacher_test_sets tt JOIN subjects s ON s.id=tt.subject_id
           WHERE tt.teacher_id=? ORDER BY tt.id DESC""",
        (teacher_row["id"],),
    ).fetchall()
    conn.close()
    return render_template("teacher_tests.html", teacher=teacher_row, tests=tests, courses=courses)


@app.route("/teacher/tests/new", methods=["POST"])
def teacher_test_new():
    if not teacher_required():
        return redirect(url_for("teacher"))
    teacher_row = current_teacher()
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    course = request.form.get("course") or ""
    try:
        class_s, subject_s = course.split(":", 1)
        class_number, subject_id = int(class_s), int(subject_s)
    except Exception:
        class_number, subject_id = None, None
    if not title or not subject_id or not class_number:
        return redirect(url_for("teacher_tests_manage", error="Maglumatlary doly dolduryň."))
    if not teacher_can_use_course(teacher_row["id"], subject_id, class_number):
        return "Bu synp/ders boýunça test taýýarlamaga rugsat ýok.", 403
    conn = get_db_connection()
    cur = conn.execute(
        """INSERT INTO teacher_test_sets(teacher_id,subject_id,class_number,title,description,is_published)
           VALUES(?,?,?,?,?,0)""",
        (teacher_row["id"], subject_id, class_number, title, description),
    )
    conn.commit()
    test_id = cur.lastrowid
    conn.close()
    return redirect(url_for("teacher_test_edit", test_id=test_id))


@app.route("/teacher/tests/<int:test_id>/edit", methods=["GET", "POST"])
def teacher_test_edit(test_id):
    if not teacher_required():
        return redirect(url_for("teacher"))
    teacher_row = current_teacher()
    conn = get_db_connection()
    test_set = conn.execute(
        """SELECT tt.*,s.name AS subject FROM teacher_test_sets tt JOIN subjects s ON s.id=tt.subject_id
           WHERE tt.id=? AND tt.teacher_id=?""",
        (test_id, teacher_row["id"]),
    ).fetchone()
    if not test_set:
        conn.close()
        return "Test tapylmady.", 404
    message = None
    error = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_question":
            correct = (request.form.get("correct_answer") or "").upper()
            fields = [request.form.get("question"), request.form.get("option_a"), request.form.get("option_b"), request.form.get("option_c"), request.form.get("option_d")]
            if correct not in {"A", "B", "C", "D"} or not all((x or "").strip() for x in fields):
                error = "Soragy we ähli jogap görnüşlerini dolduryň."
            else:
                conn.execute(
                    """INSERT INTO teacher_test_questions(test_set_id,question,option_a,option_b,option_c,option_d,correct_answer)
                       VALUES(?,?,?,?,?,?,?)""",
                    (test_id, *(x.strip() for x in fields), correct),
                )
                conn.commit()
                message = "Sorag goşuldy."
        elif action == "update_meta":
            title = (request.form.get("title") or "").strip()
            description = (request.form.get("description") or "").strip()
            publish = 1 if request.form.get("is_published") == "1" else 0
            qcount = conn.execute("SELECT COUNT(*) FROM teacher_test_questions WHERE test_set_id=?", (test_id,)).fetchone()[0]
            if publish and qcount == 0:
                error = "Iň azyndan bir sorag goşmazdan testi çap edip bolmaýar."
            elif title:
                conn.execute("UPDATE teacher_test_sets SET title=?,description=?,is_published=? WHERE id=?", (title, description, publish, test_id))
                conn.commit()
                message = "Testiň sazlamalary saklandy."
        test_set = conn.execute(
            """SELECT tt.*,s.name AS subject FROM teacher_test_sets tt JOIN subjects s ON s.id=tt.subject_id
               WHERE tt.id=? AND tt.teacher_id=?""", (test_id, teacher_row["id"])
        ).fetchone()
    questions = conn.execute("SELECT * FROM teacher_test_questions WHERE test_set_id=? ORDER BY id", (test_id,)).fetchall()
    results = conn.execute("SELECT * FROM teacher_test_results WHERE test_set_id=? ORDER BY id DESC LIMIT 30", (test_id,)).fetchall()
    conn.close()
    return render_template("teacher_test_edit.html", teacher=teacher_row, test_set=test_set, questions=questions, results=results, message=message, error=error)


@app.route("/teacher/tests/question/<int:question_id>/delete", methods=["POST"])
def teacher_test_question_delete(question_id):
    if not teacher_required():
        return redirect(url_for("teacher"))
    teacher_row = current_teacher()
    conn = get_db_connection()
    row = conn.execute(
        """SELECT q.test_set_id FROM teacher_test_questions q JOIN teacher_test_sets t ON t.id=q.test_set_id
           WHERE q.id=? AND t.teacher_id=?""", (question_id, teacher_row["id"])
    ).fetchone()
    if row:
        conn.execute("DELETE FROM teacher_test_questions WHERE id=?", (question_id,))
        conn.commit()
        test_id = row["test_set_id"]
    else:
        test_id = None
    conn.close()
    return redirect(url_for("teacher_test_edit", test_id=test_id)) if test_id else redirect(url_for("teacher_tests_manage"))


@app.route("/teacher/tests/<int:test_id>/delete", methods=["POST"])
def teacher_test_set_delete(test_id):
    if not teacher_required():
        return redirect(url_for("teacher"))
    teacher_row = current_teacher()
    conn = get_db_connection()
    conn.execute("DELETE FROM teacher_test_sets WHERE id=? AND teacher_id=?", (test_id, teacher_row["id"]))
    conn.commit()
    conn.close()
    return redirect(url_for("teacher_tests_manage"))


@app.route("/teacher-test/<int:test_id>", methods=["GET", "POST"])
def teacher_test_take(test_id):
    conn = get_db_connection()
    test_set = conn.execute(
        """SELECT tt.*,s.name AS subject,tr.full_name AS teacher_name
           FROM teacher_test_sets tt JOIN subjects s ON s.id=tt.subject_id
           JOIN teachers tr ON tr.id=tt.teacher_id
           WHERE tt.id=? AND tt.is_published=1 AND tr.is_active=1""", (test_id,)
    ).fetchone()
    if not test_set:
        conn.close()
        return "Test tapylmady ýa-da entek çap edilmedi.", 404
    questions = conn.execute("SELECT * FROM teacher_test_questions WHERE test_set_id=? ORDER BY id", (test_id,)).fetchall()
    result = None
    details = []
    student_name = (request.form.get("student_name") or "").strip()
    if request.method == "POST" and questions:
        score = 0
        for q in questions:
            selected = (request.form.get(f"q_{q['id']}") or "").upper()
            correct = q["correct_answer"].upper()
            ok = selected == correct
            if ok:
                score += 1
            details.append({"question": q["question"], "selected": selected, "correct": correct, "ok": ok})
        percent = round(score * 100 / len(questions))
        result = {"score": score, "total": len(questions), "percent": percent}
        if student_name:
            conn.execute(
                "INSERT INTO teacher_test_results(student_name,test_set_id,score,total,percent) VALUES(?,?,?,?,?)",
                (student_name, test_id, score, len(questions), percent),
            )
            conn.commit()
    conn.close()
    return render_template("teacher_test_take.html", test_set=test_set, questions=questions, result=result, details=details, student_name=student_name)


@app.route("/library")
def library():
    class_number = request.args.get("class", "")
    subject = request.args.get("subject", "")
    q = (request.args.get("q") or "").strip()
    books = list_books(class_number or None, subject or None, q or None)
    return render_template(
        "library.html",
        books=books,
        subjects_by_class=get_subjects_by_class(),
        selected_class=class_number,
        selected_subject=subject,
        q=q,
    )


@app.route("/library/book/<int:book_id>")
def library_book(book_id):
    conn = get_db_connection()
    book = conn.execute(
        """SELECT b.*, s.name AS subject FROM books b JOIN subjects s ON s.id=b.subject_id WHERE b.id=?""",
        (book_id,),
    ).fetchone()
    chunks = conn.execute(
        "SELECT page_number,chunk_order,text FROM book_chunks WHERE book_id=? ORDER BY chunk_order LIMIT 250",
        (book_id,),
    ).fetchall() if book else []
    conn.close()
    if not book:
        return "Kitap tapylmady.", 404
    return render_template("book_view.html", book=book, chunks=chunks)


@app.route("/library/file/<int:book_id>")
def library_file(book_id):
    conn = get_db_connection()
    book = conn.execute("SELECT stored_name FROM books WHERE id=?", (book_id,)).fetchone()
    conn.close()
    if not book:
        return "Kitap tapylmady.", 404
    return send_from_directory(BOOK_UPLOAD_DIR, book["stored_name"], as_attachment=False)


@app.route("/teacher/books", methods=["GET", "POST"])
def teacher_books():
    if not teacher_required():
        return redirect(url_for("teacher"))
    teacher_row = current_teacher()
    message = None
    error = None
    if request.method == "POST":
        file = request.files.get("book_file")
        title = (request.form.get("title") or "").strip()
        author = (request.form.get("author") or "").strip()
        subject = (request.form.get("subject") or "").strip()
        class_number = request.form.get("class_number", type=int)
        year = request.form.get("year", type=int)
        uploaded_by = teacher_row["username"]
        if not file or not file.filename:
            error = "Kitap faýly saýlanmady."
        elif not title or not subject or not class_number:
            error = "Kitabyň adyny, synpyny we dersini dolduryň."
        elif not allowed_book_file(file.filename):
            error = "Häzir PDF, DOCX, TXT ýa-da RTF kitaplar kabul edilýär."
        else:
            sid = subject_id_for(subject)
            if not sid:
                error = "Saýlanan ders tapylmady."
            elif not teacher_can_use_course(teacher_row["id"], sid, class_number):
                error = "Siz diňe öz okadýan synpyňyz we dersiňiz üçin kitap goşup bilersiňiz."
            else:
                ext = file.filename.rsplit(".", 1)[1].lower()
                base = secure_filename(Path(file.filename).stem) or "book"
                stored_name = f"{class_number}_{sid}_{int(__import__('time').time())}_{base}.{ext}"
                target = BOOK_UPLOAD_DIR / stored_name
                try:
                    file.save(target)
                    extracted = extract_book_text(target, ext)
                    conn = get_db_connection()
                    cur = conn.execute(
                        """INSERT INTO books(title,author,subject_id,class_number,year,original_name,stored_name,file_type,uploaded_by)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (title, author, sid, class_number, year, file.filename, stored_name, ext, uploaded_by),
                    )
                    book_id = cur.lastrowid
                    for order, (page_no, text_value) in enumerate(extracted, start=1):
                        conn.execute(
                            "INSERT INTO book_chunks(book_id,page_number,chunk_order,text) VALUES(?,?,?,?)",
                            (book_id, page_no, order, text_value),
                        )
                    conn.commit()
                    conn.close()
                    message = f"Kitap goşuldy. {len(extracted)} tekst bölegi indekslendi."
                except Exception as exc:
                    if target.exists():
                        target.unlink(missing_ok=True)
                    error = f"Kitaby işläp bolmady: {exc}"
    conn = get_db_connection()
    books = conn.execute(
        """SELECT b.*,s.name AS subject FROM books b JOIN subjects s ON s.id=b.subject_id
           WHERE b.uploaded_by=? ORDER BY b.id DESC""", (teacher_row["username"],)
    ).fetchall()
    conn.close()
    conn = get_db_connection()
    teacher_courses = conn.execute(
        """SELECT tc.class_number,s.name AS subject,s.icon FROM teacher_courses tc
           JOIN subjects s ON s.id=tc.subject_id WHERE tc.teacher_id=?
           ORDER BY tc.class_number,s.name""", (teacher_row["id"],)
    ).fetchall()
    conn.close()
    return render_template(
        "teacher_books.html",
        books=books,
        teacher_courses=teacher_courses,
        message=message,
        error=error,
    )


@app.route("/teacher/books/delete/<int:book_id>", methods=["POST"])
def teacher_book_delete(book_id):
    if not teacher_required():
        return redirect(url_for("teacher"))
    teacher_row = current_teacher()
    conn = get_db_connection()
    book = conn.execute("SELECT stored_name FROM books WHERE id=? AND uploaded_by=?", (book_id, teacher_row["username"])).fetchone()
    if book:
        conn.execute("DELETE FROM book_chunks WHERE book_id=?", (book_id,))
        conn.execute("DELETE FROM books WHERE id=?", (book_id,))
        conn.commit()
        path = BOOK_UPLOAD_DIR / book["stored_name"]
        path.unlink(missing_ok=True)
    conn.close()
    return redirect(url_for("teacher_books"))


@app.route("/generate-practical", methods=["POST"])
def generate_practical():
    topic_id = request.form.get("topic_id")
    topic = get_topic(topic_id)
    if not topic:
        return "Tema tapylmady.", 404
    cached = get_cached_lesson(topic["id"])
    lesson_text = cached["lesson_text"] if cached else None
    book_context, book_sources = book_context_for_topic(topic)
    instructions = """Sen AI MEKDEP okuw platformasynda mugallym kömekçisi.
Saýlanan synp, ders we tema üçin okuwçynyň özbaşdak ýerine ýetirip biljek täze amaly iş taýýarla.
Kitapdan getirilen maglumat bar bolsa, ony mazmun taýdan esas hökmünde ulan, ýöne kitapdaky gönükmäni göni gaýtalama.
Amaly iş synp derejesine laýyk, howpsuz, anyk we ýerine ýetiriş ädimleri bilen bolsun.
Soňky bölümde okuwçy öz işini nädip barlamalydygyny ýaz."""
    user_input = f"""{curriculum_context(topic)}

Elektron kitaphanadan degişli maglumat:
{book_context or 'Degişli kitap bölegi tapylmady.'}

Täze amaly iş generirle."""
    practical_ai, error = call_openai(instructions, user_input)
    if error:
        practical_ai = error
    return render_template(
        "study.html",
        topic=topic,
        lesson_text=lesson_text,
        lesson_error=None,
        lesson_cached=bool(cached),
        ai_answer=None,
        last_question="",
        book_sources=book_sources,
        practical_ai=practical_ai,
    )



# ==================================================
# ADMIN SAHYPALARY
# ==================================================
@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    conn = get_db_connection()
    admin_count = conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
    conn.close()
    if admin_count == 0:
        return redirect(url_for("admin_setup"))
    if admin_required():
        return redirect(url_for("admin_dashboard"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM admins WHERE username=? COLLATE NOCASE AND is_active=1",
            (username,),
        ).fetchone()
        conn.close()
        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session["admin_id"] = row["id"]
            session["admin_name"] = row["full_name"]
            log_admin_action("login", "Admin ulgama girdi")
            return redirect(url_for("admin_dashboard"))
        error = "Login ýa-da parol nädogry."
    return render_template("admin_login.html", error=error)


@app.route("/admin/setup", methods=["GET", "POST"])
def admin_setup():
    conn = get_db_connection()
    count = conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
    conn.close()
    if count:
        return redirect(url_for("admin_login"))
    error = None
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        if len(full_name) < 3:
            error = "Adminiň adyny doly ýazyň."
        elif len(username) < 3:
            error = "Login azyndan 3 belgiden ybarat bolmaly."
        elif len(password) < 8:
            error = "Admin paroly azyndan 8 belgiden ybarat bolmaly."
        elif password != confirm:
            error = "Parollar gabat gelenok."
        else:
            conn = get_db_connection()
            try:
                cur = conn.execute(
                    "INSERT INTO admins(full_name,username,password_hash) VALUES(?,?,?)",
                    (full_name, username, generate_password_hash(password)),
                )
                conn.commit()
                admin_id = cur.lastrowid
                conn.close()
                session.clear()
                session["admin_id"] = admin_id
                session["admin_name"] = full_name
                log_admin_action("setup", "Ilkinji admin hasaby döredildi")
                return redirect(url_for("admin_dashboard"))
            except sqlite3.IntegrityError:
                conn.rollback(); conn.close()
                error = "Bu login eýýäm ulanylýar."
    return render_template("admin_setup.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    if admin_required():
        log_admin_action("logout", "Admin ulgamdan çykdy")
    session.clear()
    return redirect(url_for("home"))


@app.route("/admin/dashboard")
def admin_dashboard():
    if not admin_required():
        return redirect(url_for("admin_login"))
    admin = current_admin()
    conn = get_db_connection()
    stats = {
        "teachers": conn.execute("SELECT COUNT(*) FROM teachers").fetchone()[0],
        "active_teachers": conn.execute("SELECT COUNT(*) FROM teachers WHERE is_active=1").fetchone()[0],
        "books": conn.execute("SELECT COUNT(*) FROM books").fetchone()[0],
        "teacher_tests": conn.execute("SELECT COUNT(*) FROM teacher_test_sets").fetchone()[0],
        "published_tests": conn.execute("SELECT COUNT(*) FROM teacher_test_sets WHERE is_published=1").fetchone()[0],
        "results": conn.execute("SELECT COUNT(*) FROM teacher_test_results").fetchone()[0],
        "subjects": conn.execute("SELECT COUNT(*) FROM subjects").fetchone()[0],
        "topics": conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0],
    }
    recent_teachers = conn.execute("SELECT * FROM teachers ORDER BY id DESC LIMIT 6").fetchall()
    recent_logs = conn.execute(
        """SELECT l.*,a.full_name AS admin_name FROM admin_logs l
           LEFT JOIN admins a ON a.id=l.admin_id ORDER BY l.id DESC LIMIT 12"""
    ).fetchall()
    conn.close()
    return render_template("admin_dashboard.html", admin=admin, stats=stats,
                           recent_teachers=recent_teachers, recent_logs=recent_logs)


@app.route("/admin/teachers")
def admin_teachers():
    if not admin_required():
        return redirect(url_for("admin_login"))
    q = (request.args.get("q") or "").strip()
    conn = get_db_connection()
    sql = """SELECT t.*,
        (SELECT COUNT(*) FROM teacher_courses tc WHERE tc.teacher_id=t.id) AS course_count,
        (SELECT COUNT(*) FROM teacher_test_sets ts WHERE ts.teacher_id=t.id) AS test_count,
        (SELECT COUNT(*) FROM books b WHERE b.uploaded_by=t.username) AS book_count
        FROM teachers t WHERE 1=1"""
    params = []
    if q:
        sql += " AND (lower(t.full_name) LIKE lower(?) OR lower(t.username) LIKE lower(?))"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY t.id DESC"
    teachers = conn.execute(sql, params).fetchall()
    conn.close()
    return render_template("admin_teachers.html", teachers=teachers, q=q)


@app.route("/admin/teachers/<int:teacher_id>", methods=["GET", "POST"])
def admin_teacher_edit(teacher_id):
    if not admin_required():
        return redirect(url_for("admin_login"))
    conn = get_db_connection()
    teacher = conn.execute("SELECT * FROM teachers WHERE id=?", (teacher_id,)).fetchone()
    if not teacher:
        conn.close(); return "Mugallym tapylmady.", 404
    message = None; error = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "profile":
            full_name = (request.form.get("full_name") or "").strip()
            username = (request.form.get("username") or "").strip()
            if len(full_name) < 3 or len(username) < 3:
                error = "Ady we logini dogry dolduryň."
            else:
                try:
                    conn.execute("UPDATE teachers SET full_name=?,username=? WHERE id=?", (full_name, username, teacher_id))
                    conn.commit(); message = "Mugallymyň maglumatlary täzelendi."
                    log_admin_action("teacher_update", f"teacher_id={teacher_id}")
                except sqlite3.IntegrityError:
                    error = "Bu login başga mugallym tarapyndan ulanylýar."
        elif action == "password":
            password = request.form.get("new_password") or ""
            if len(password) < 6:
                error = "Täze parol azyndan 6 belgiden ybarat bolmaly."
            else:
                conn.execute("UPDATE teachers SET password_hash=? WHERE id=?", (generate_password_hash(password), teacher_id))
                conn.commit(); message = "Täze parol goýuldy."
                log_admin_action("teacher_password_reset", f"teacher_id={teacher_id}")
        elif action == "courses":
            selected = []
            for value in request.form.getlist("courses"):
                try:
                    grade_s, sid_s = value.split(":", 1)
                    grade, sid = int(grade_s), int(sid_s)
                    valid = conn.execute("SELECT 1 FROM subject_classes WHERE class_number=? AND subject_id=?", (grade, sid)).fetchone()
                    if valid: selected.append((grade, sid))
                except Exception:
                    pass
            conn.execute("DELETE FROM teacher_courses WHERE teacher_id=?", (teacher_id,))
            for grade, sid in sorted(set(selected)):
                conn.execute("INSERT OR IGNORE INTO teacher_courses(teacher_id,subject_id,class_number) VALUES(?,?,?)", (teacher_id, sid, grade))
            conn.commit(); message = "Mugallymyň synp-dersleri täzelendi."
            log_admin_action("teacher_courses_update", f"teacher_id={teacher_id}; count={len(set(selected))}")
        teacher = conn.execute("SELECT * FROM teachers WHERE id=?", (teacher_id,)).fetchone()
    selected_rows = conn.execute("SELECT class_number,subject_id FROM teacher_courses WHERE teacher_id=?", (teacher_id,)).fetchall()
    selected = {f"{r['class_number']}:{r['subject_id']}" for r in selected_rows}
    tests = conn.execute("""SELECT tt.*,s.name AS subject FROM teacher_test_sets tt JOIN subjects s ON s.id=tt.subject_id
                            WHERE tt.teacher_id=? ORDER BY tt.id DESC LIMIT 20""", (teacher_id,)).fetchall()
    conn.close()
    return render_template("admin_teacher_edit.html", teacher=teacher, selected=selected,
                           courses_by_class=admin_course_options(), tests=tests, message=message, error=error)


@app.route("/admin/teachers/<int:teacher_id>/toggle", methods=["POST"])
def admin_teacher_toggle(teacher_id):
    if not admin_required(): return redirect(url_for("admin_login"))
    conn = get_db_connection()
    row = conn.execute("SELECT is_active,full_name FROM teachers WHERE id=?", (teacher_id,)).fetchone()
    if row:
        new_value = 0 if row["is_active"] else 1
        conn.execute("UPDATE teachers SET is_active=? WHERE id=?", (new_value, teacher_id)); conn.commit()
        log_admin_action("teacher_toggle", f"teacher_id={teacher_id}; active={new_value}")
    conn.close()
    return redirect(request.referrer or url_for("admin_teachers"))


@app.route("/admin/teachers/<int:teacher_id>/delete", methods=["POST"])
def admin_teacher_delete(teacher_id):
    if not admin_required(): return redirect(url_for("admin_login"))
    conn = get_db_connection()
    row = conn.execute("SELECT full_name FROM teachers WHERE id=?", (teacher_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM teachers WHERE id=?", (teacher_id,)); conn.commit()
        log_admin_action("teacher_delete", f"teacher_id={teacher_id}; name={row['full_name']}")
    conn.close()
    return redirect(url_for("admin_teachers"))


@app.route("/admin/books")
def admin_books():
    if not admin_required(): return redirect(url_for("admin_login"))
    conn = get_db_connection()
    books = conn.execute("""SELECT b.*,s.name AS subject FROM books b JOIN subjects s ON s.id=b.subject_id
                            ORDER BY b.id DESC""").fetchall()
    conn.close()
    return render_template("admin_books.html", books=books)


@app.route("/admin/books/<int:book_id>/delete", methods=["POST"])
def admin_book_delete(book_id):
    if not admin_required(): return redirect(url_for("admin_login"))
    conn = get_db_connection()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if book:
        conn.execute("DELETE FROM books WHERE id=?", (book_id,)); conn.commit()
        try:
            (BOOK_UPLOAD_DIR / book["stored_name"]).unlink(missing_ok=True)
        except Exception:
            pass
        log_admin_action("book_delete", f"book_id={book_id}; title={book['title']}")
    conn.close()
    return redirect(url_for("admin_books"))


@app.route("/admin/tests")
def admin_tests():
    if not admin_required(): return redirect(url_for("admin_login"))
    conn = get_db_connection()
    tests = conn.execute("""SELECT tt.*,s.name AS subject,t.full_name AS teacher_name,
        (SELECT COUNT(*) FROM teacher_test_questions q WHERE q.test_set_id=tt.id) AS question_count,
        (SELECT COUNT(*) FROM teacher_test_results r WHERE r.test_set_id=tt.id) AS result_count
        FROM teacher_test_sets tt JOIN subjects s ON s.id=tt.subject_id JOIN teachers t ON t.id=tt.teacher_id
        ORDER BY tt.id DESC""").fetchall()
    conn.close()
    return render_template("admin_tests.html", tests=tests)


@app.route("/admin/tests/<int:test_id>/toggle", methods=["POST"])
def admin_test_toggle(test_id):
    if not admin_required(): return redirect(url_for("admin_login"))
    conn = get_db_connection()
    row = conn.execute("SELECT is_published FROM teacher_test_sets WHERE id=?", (test_id,)).fetchone()
    if row:
        new_value = 0 if row["is_published"] else 1
        if new_value:
            qcount = conn.execute("SELECT COUNT(*) FROM teacher_test_questions WHERE test_set_id=?", (test_id,)).fetchone()[0]
            if qcount == 0:
                conn.close(); return redirect(url_for("admin_tests"))
        conn.execute("UPDATE teacher_test_sets SET is_published=? WHERE id=?", (new_value, test_id)); conn.commit()
        log_admin_action("test_toggle", f"test_id={test_id}; published={new_value}")
    conn.close()
    return redirect(url_for("admin_tests"))


@app.route("/admin/tests/<int:test_id>/delete", methods=["POST"])
def admin_test_delete(test_id):
    if not admin_required(): return redirect(url_for("admin_login"))
    conn = get_db_connection()
    conn.execute("DELETE FROM teacher_test_sets WHERE id=?", (test_id,)); conn.commit(); conn.close()
    log_admin_action("test_delete", f"test_id={test_id}")
    return redirect(url_for("admin_tests"))


@app.route("/admin/results")
def admin_results():
    if not admin_required(): return redirect(url_for("admin_login"))
    conn = get_db_connection()
    rows = conn.execute("""SELECT r.*,tt.title,s.name AS subject,tt.class_number,t.full_name AS teacher_name
        FROM teacher_test_results r JOIN teacher_test_sets tt ON tt.id=r.test_set_id
        JOIN subjects s ON s.id=tt.subject_id JOIN teachers t ON t.id=tt.teacher_id
        ORDER BY r.id DESC LIMIT 500""").fetchall()
    conn.close()
    return render_template("admin_results.html", rows=rows)


@app.route("/admin/backup")
def admin_backup():
    if not admin_required(): return redirect(url_for("admin_login"))
    log_admin_action("backup", "Maglumat bazasynyň nusgasy alyndy")
    return send_file(DB_NAME, as_attachment=True, download_name="ai_mekdep_backup.db")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
