# -*- coding: utf-8 -*-
import sqlite3
import os
from pathlib import Path
from curriculum_data import CURRICULUM, CURRICULUM_VERSION

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AI_MEKDEP_DATA_DIR", str(BASE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_NAME = DATA_DIR / "ai_mekdep.db"


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn):
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS subjects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, icon TEXT, source_year INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS classes (id INTEGER PRIMARY KEY AUTOINCREMENT, class_number INTEGER NOT NULL UNIQUE)")
    c.execute("""CREATE TABLE IF NOT EXISTS subject_classes (
      id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL, class_number INTEGER NOT NULL,
      total_hours INTEGER, track TEXT, source_year INTEGER, UNIQUE(subject_id,class_number),
      FOREIGN KEY(subject_id) REFERENCES subjects(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS topics (
      id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL, class_number INTEGER NOT NULL,
      section TEXT, topic TEXT NOT NULL, hours INTEGER, topic_order INTEGER,
      explanation TEXT, example_text TEXT, practical_task TEXT, source_year INTEGER,
      UNIQUE(subject_id,class_number,topic_order), FOREIGN KEY(subject_id) REFERENCES subjects(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS tests (
      id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id INTEGER NOT NULL, question TEXT NOT NULL,
      option_a TEXT NOT NULL, option_b TEXT NOT NULL, option_c TEXT NOT NULL, option_d TEXT NOT NULL,
      correct_answer TEXT NOT NULL, FOREIGN KEY(topic_id) REFERENCES topics(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS ai_content (
      topic_id INTEGER PRIMARY KEY, lesson_text TEXT NOT NULL, model TEXT, generated_at TEXT,
      FOREIGN KEY(topic_id) REFERENCES topics(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS test_results (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_name TEXT NOT NULL,
      class_number INTEGER NOT NULL,
      subject TEXT NOT NULL,
      topic_id INTEGER NOT NULL,
      topic_name TEXT NOT NULL,
      score INTEGER NOT NULL,
      total INTEGER NOT NULL,
      percent INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_results_student ON test_results(student_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_results_topic ON test_results(topic_id)")
    c.execute("""CREATE TABLE IF NOT EXISTS books (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      author TEXT,
      subject_id INTEGER NOT NULL,
      class_number INTEGER NOT NULL,
      year INTEGER,
      original_name TEXT NOT NULL,
      stored_name TEXT NOT NULL,
      file_type TEXT NOT NULL,
      uploaded_by TEXT,
      uploaded_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
      FOREIGN KEY(subject_id) REFERENCES subjects(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS book_chunks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book_id INTEGER NOT NULL,
      page_number INTEGER,
      chunk_order INTEGER NOT NULL,
      text TEXT NOT NULL,
      FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_books_class_subject ON books(class_number,subject_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_book_chunks_book ON book_chunks(book_id,chunk_order)")

    # Mugallymlaryň şahsy hasaplary
    c.execute("""CREATE TABLE IF NOT EXISTS teachers (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      full_name TEXT NOT NULL,
      username TEXT NOT NULL UNIQUE COLLATE NOCASE,
      password_hash TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
      is_active INTEGER NOT NULL DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS teacher_subjects (
      teacher_id INTEGER NOT NULL, subject_id INTEGER NOT NULL,
      PRIMARY KEY(teacher_id,subject_id),
      FOREIGN KEY(teacher_id) REFERENCES teachers(id) ON DELETE CASCADE,
      FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE)""")
    c.execute("""CREATE TABLE IF NOT EXISTS teacher_classes (
      teacher_id INTEGER NOT NULL, class_number INTEGER NOT NULL,
      PRIMARY KEY(teacher_id,class_number),
      FOREIGN KEY(teacher_id) REFERENCES teachers(id) ON DELETE CASCADE)""")
    c.execute("""CREATE TABLE IF NOT EXISTS teacher_courses (
      teacher_id INTEGER NOT NULL, subject_id INTEGER NOT NULL, class_number INTEGER NOT NULL,
      PRIMARY KEY(teacher_id,subject_id,class_number),
      FOREIGN KEY(teacher_id) REFERENCES teachers(id) ON DELETE CASCADE,
      FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE)""")

    # Mugallymyň özi taýýarlaýan testleri
    c.execute("""CREATE TABLE IF NOT EXISTS teacher_test_sets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      teacher_id INTEGER NOT NULL,
      subject_id INTEGER NOT NULL,
      class_number INTEGER NOT NULL,
      title TEXT NOT NULL,
      description TEXT,
      is_published INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
      FOREIGN KEY(teacher_id) REFERENCES teachers(id) ON DELETE CASCADE,
      FOREIGN KEY(subject_id) REFERENCES subjects(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS teacher_test_questions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      test_set_id INTEGER NOT NULL,
      question TEXT NOT NULL,
      option_a TEXT NOT NULL, option_b TEXT NOT NULL,
      option_c TEXT NOT NULL, option_d TEXT NOT NULL,
      correct_answer TEXT NOT NULL,
      FOREIGN KEY(test_set_id) REFERENCES teacher_test_sets(id) ON DELETE CASCADE)""")
    c.execute("""CREATE TABLE IF NOT EXISTS teacher_test_results (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_name TEXT NOT NULL,
      test_set_id INTEGER NOT NULL,
      score INTEGER NOT NULL, total INTEGER NOT NULL, percent INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
      FOREIGN KEY(test_set_id) REFERENCES teacher_test_sets(id) ON DELETE CASCADE)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_teacher_tests_course ON teacher_test_sets(class_number,subject_id,is_published)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_teacher_test_results_set ON teacher_test_results(test_set_id)")

    # Platforma administratorlarynyň hasaplary
    c.execute("""CREATE TABLE IF NOT EXISTS admins (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      full_name TEXT NOT NULL,
      username TEXT NOT NULL UNIQUE COLLATE NOCASE,
      password_hash TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
      is_active INTEGER NOT NULL DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS admin_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      admin_id INTEGER,
      action TEXT NOT NULL,
      details TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
      FOREIGN KEY(admin_id) REFERENCES admins(id) ON DELETE SET NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_admin_logs_created ON admin_logs(created_at)")
    conn.commit()


def rebuild_curriculum(conn):
    c = conn.cursor()
    # Netijeleri pozmaýarys; diňe maksatnama maglumatlaryny täzeläp gurýarys.
    c.execute("DELETE FROM ai_content")
    c.execute("DELETE FROM tests")
    c.execute("DELETE FROM topics")
    c.execute("DELETE FROM subject_classes")
    # Elektron kitaphanadaky kitaplaryň subject_id baglanyşyklaryny goramak üçin
    # subjects tablisasy pozulmaýar; dersler aşakda täzelenýär/goşulýar.
    c.execute("DELETE FROM classes")
    for grade in range(5, 13):
        c.execute("INSERT INTO classes(class_number) VALUES (?)", (grade,))
    for subject_name, sd in CURRICULUM.items():
        c.execute(
            """INSERT INTO subjects(name,icon,source_year) VALUES(?,?,?)
               ON CONFLICT(name) DO UPDATE SET icon=excluded.icon, source_year=excluded.source_year""",
            (subject_name, sd.get("icon"), sd.get("year")),
        )
        sid = c.execute("SELECT id FROM subjects WHERE name=?", (subject_name,)).fetchone()[0]
        for grade, gd in sd["grades"].items():
            c.execute(
                "INSERT INTO subject_classes(subject_id,class_number,total_hours,track,source_year) VALUES(?,?,?,?,?)",
                (sid, int(grade), gd["total_hours"], sd.get("track"), sd.get("year")),
            )
            order = 0
            for unit_name, unit_hours in gd["units"]:
                for lesson_no in range(1, int(unit_hours) + 1):
                    order += 1
                    if int(unit_hours) == 1:
                        topic_name = unit_name
                    else:
                        topic_name = f"{unit_name} — {lesson_no}-nji sapak"
                    explanation = (
                        f"{subject_name} dersiniň {grade}-nji synp okuw maksatnamasy. "
                        f"Bölüm/tema: {unit_name}. Maksatnamada bu tema üçin {unit_hours} sagat berilýär. "
                        f"Bu şol temanyň {lesson_no}-nji sapagydyr."
                    )
                    c.execute(
                        """INSERT INTO topics(subject_id,class_number,section,topic,hours,topic_order,explanation,source_year)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (sid, int(grade), unit_name, topic_name, 1, order, explanation, sd.get("year")),
                    )
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('curriculum_version',?)", (CURRICULUM_VERSION,))
    conn.commit()


def add_sample_tests(conn):
    c = conn.cursor()
    row = c.execute(
        """SELECT t.id FROM topics t JOIN subjects s ON s.id=t.subject_id
           WHERE s.name='Informatika' AND t.class_number=5 AND t.section='Operasion sistema'
           ORDER BY t.topic_order LIMIT 1"""
    ).fetchone()
    if not row:
        return
    topic_id = row[0]
    explanation = """Operasion sistema kompýuteriň esasy programma üpjünçiligidir.
Ol kompýuteriň enjamlarynyň we programmalarynyň işini dolandyrýar.

Operasion sistema ulanyjy bilen kompýuteriň arasynda aragatnaşyk döredýär.
Ol faýllary açmaga, programmalary işletmäge, penjireler bilen işlemäge we kompýuteri dolandyrmaga mümkinçilik berýär.

Operasion sistemalara Windows, Linux, macOS ýaly sistemalar mysal bolup biler."""
    example = "Mysal:\nOkuwçy kompýuteri açanda Windows operasion sistemasy işe başlaýar. Soňra iş stoly peýda bolýar."
    practical = """Amaly iş:
1. Kompýuteri aç.
2. Iş stoluna seret.
3. Start menýusyny aç.
4. Bir programmany işe giriz.
5. Programmanyň penjiresini kiçelt we ulalt.
6. Programmany ýap."""
    c.execute(
        "UPDATE topics SET explanation=?,example_text=?,practical_task=? WHERE id=?",
        (explanation, example, practical, topic_id),
    )
    tests = [
        ("Operasion sistema näme?", "Kompýuteriň esasy programma üpjünçiligi", "Diňe bir oýun", "Printeriň bir bölegi", "Internet sahypasy", "A"),
        ("Operasion sistemanyň esasy wezipesi haýsy?", "Diňe surat çekmek", "Kompýuteriň enjamlaryny we programmalaryny dolandyrmak", "Diňe internet açmak", "Diňe tekst ýazmak", "B"),
        ("Haýsysy operasion sistema mysalydyr?", "Paint", "Calculator", "Windows", "Word", "C"),
        ("Windows işe başlananda ekranda ilki näme peýda bolup biler?", "Iş stoly", "Printer", "Klawiatura", "Skaner", "A"),
        ("Operasion sistema ulanyjy bilen nämäniň arasynda aragatnaşyk döredýär?", "Diňe printeriň arasynda", "Kompýuter bilen ulanyjynyň arasynda", "Diňe internet bilen", "Diňe faýllaryň arasynda", "B"),
    ]
    if c.execute("SELECT COUNT(*) FROM tests WHERE topic_id=?", (topic_id,)).fetchone()[0] == 0:
        for t in tests:
            c.execute(
                "INSERT INTO tests(topic_id,question,option_a,option_b,option_c,option_d,correct_answer) VALUES(?,?,?,?,?,?,?)",
                (topic_id, *t),
            )
    conn.commit()


def initialize_database():
    conn = get_connection()
    create_schema(conn)
    row = conn.execute("SELECT value FROM meta WHERE key='curriculum_version'").fetchone()
    if not row or row[0] != CURRICULUM_VERSION:
        rebuild_curriculum(conn)
        add_sample_tests(conn)
    conn.close()


if __name__ == "__main__":
    initialize_database()
    conn = get_connection()
    print("AI MEKDEP maglumat bazasy taýýar.")
    print("Synp-ders maksatnamalary:")
    for row in conn.execute(
        """SELECT sc.class_number,s.name,sc.total_hours,COUNT(t.id)
           FROM subject_classes sc JOIN subjects s ON s.id=sc.subject_id
           LEFT JOIN topics t ON t.subject_id=s.id AND t.class_number=sc.class_number
           GROUP BY sc.class_number,s.name,sc.total_hours ORDER BY sc.class_number,s.name"""
    ):
        print(f"{row[0]} synp | {row[1]} | {row[2]} sagat | {row[3]} sapak")
    conn.close()
