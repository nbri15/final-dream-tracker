# app.py
from flask import Flask, render_template, redirect, url_for, request, flash, send_file
from flask_migrate import Migrate
from flask_login import (
    LoginManager, login_user, login_required, current_user, logout_user
)
from sqlalchemy import case, func, inspect, text, and_, or_
from sqlalchemy.exc import NoSuchTableError
from config import Config
from models import (
    db, SchoolClass, Teacher, TeacherClass, Pupil, Result, WritingResult, TermConfig, AcademicYear,
    Assessment, AssessmentQuestion, PupilQuestionScore, Intervention,
    SatsHeader, SatsScore, PupilClassHistory, PaperTemplate, PaperTemplateQuestion,
    PupilReportNote, PupilProfile
)
from forms import (
    LoginForm, PupilForm, ResultForm, TermSettingsForm,
    YearForm, SetCurrentYearForm,
    CSVUploadResultsForm, ClassSettingsForm,
    AdminUserCreateForm, AdminUserEditForm, AdminResetPasswordForm
)
import io
import csv
import re
import json
from datetime import datetime, date
from urllib.parse import urlencode

from flask import jsonify, abort, make_response

try:
    from weasyprint import HTML
except Exception:  # optional dependency in some local environments
    HTML = None


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # ---- DB & Login setup
    db.init_app(app)
    Migrate(app, db)
    login_manager = LoginManager(app)
    login_manager.login_view = "login"

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(Teacher, int(user_id))

    # ---- Helpers
    TERMS = ["Autumn", "Spring", "Summer"]
    SUBJECTS = ("maths", "reading", "spag")
    PAPERS = {
        "maths": ["Arithmetic", "Reasoning"],
        "reading": ["Paper 1", "Paper 2"],
        "spag": ["Spelling", "Grammar"],
    }

    def normalize_subject(value: str) -> str:
        s = (value or "").strip().lower()
        if s not in SUBJECTS:
            abort(404)
        return s

    def subject_display(subject: str) -> str:
        labels = {"maths": "Maths", "reading": "Reading", "spag": "SPaG"}
        return labels.get(subject, subject.title())

    def paper_labels_for(subject: str):
        return tuple(PAPERS[subject])

    def get_current_year():
        y = AcademicYear.query.filter_by(is_current=True).first()
        if not y:
            y = AcademicYear.query.order_by(AcademicYear.id.asc()).first()
        return y

    def ensure_default_year():
        # Create a default year if none exists
        if AcademicYear.query.count() == 0:
            y = AcademicYear(label="2025/26", is_current=True)
            db.session.add(y)
            db.session.commit()
        return get_current_year()

    def ensure_archive_column():
        try:
            cols = {c["name"] for c in inspect(db.engine).get_columns("classes")}
        except NoSuchTableError:
            return
        if "year_group" not in cols:
            db.session.execute(text("ALTER TABLE classes ADD COLUMN year_group INTEGER"))
            db.session.commit()
        if "is_archived" not in cols:
            db.session.execute(text("ALTER TABLE classes ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT 0"))
            db.session.commit()
        if "is_archive" not in cols:
            db.session.execute(text("ALTER TABLE classes ADD COLUMN is_archive BOOLEAN NOT NULL DEFAULT 0"))
            db.session.commit()
            db.session.execute(text("UPDATE classes SET is_archive = is_archived WHERE is_archived = 1"))
            db.session.commit()

    def ensure_single_archive_class():
        """Guarantee exactly one archive class and keep it out of normal year groups."""
        all_archive_like = (SchoolClass.query
                            .filter((SchoolClass.is_archive.is_(True)) |
                                    (func.lower(SchoolClass.name) == "archive"))
                            .order_by(SchoolClass.id.asc())
                            .all())

        if all_archive_like:
            archive = all_archive_like[0]
            archive.name = "Archive"
            archive.year_group = None
            archive.is_archived = True
            archive.is_archive = True
            for extra in all_archive_like[1:]:
                extra.is_archive = False
                extra.is_archived = False
                if extra.year_group == 0:
                    extra.year_group = None
        else:
            archive = SchoolClass(name="Archive", year_group=None, is_archived=True, is_archive=True)
            db.session.add(archive)

        db.session.commit()
        return archive

    def ensure_result_subject_column():
        try:
            cols = {c["name"] for c in inspect(db.engine).get_columns("results")}
        except NoSuchTableError:
            return
        if "subject" not in cols:
            db.session.execute(text("ALTER TABLE results ADD COLUMN subject TEXT NOT NULL DEFAULT 'maths'"))
            db.session.commit()
        db.session.execute(text("UPDATE results SET subject = 'maths' WHERE subject IS NULL OR TRIM(subject) = ''"))
        db.session.commit()
        db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_results_subject_year_term_pupil ON results (subject, academic_year_id, term, pupil_id)"))
        db.session.commit()

    def ensure_assessment_subject_paper_columns():
        try:
            cols = {c["name"] for c in inspect(db.engine).get_columns("assessments")}
        except NoSuchTableError:
            return
        if "subject" not in cols:
            db.session.execute(text("ALTER TABLE assessments ADD COLUMN subject TEXT NOT NULL DEFAULT 'maths'"))
            db.session.commit()
        if "paper" not in cols:
            db.session.execute(text("ALTER TABLE assessments ADD COLUMN paper TEXT NOT NULL DEFAULT 'Arithmetic'"))
            db.session.commit()
        db.session.execute(text("UPDATE assessments SET subject = 'maths' WHERE subject IS NULL OR TRIM(subject) = ''"))
        db.session.execute(text("UPDATE assessments SET paper = 'Arithmetic' WHERE paper IS NULL OR TRIM(paper) = ''"))
        db.session.execute(text("UPDATE assessments SET paper = 'Reasoning' WHERE lower(title) LIKE '%reasoning%'"))
        db.session.commit()
        db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_assessments_subject_term_year_class ON assessments (subject, term, academic_year_id, class_id)"))
        db.session.commit()

    def ensure_termconfig_columns():
        try:
            cols = {c["name"] for c in inspect(db.engine).get_columns("term_configs")}
        except NoSuchTableError:
            return
        needed = {
            "reading_p1_max": "ALTER TABLE term_configs ADD COLUMN reading_p1_max FLOAT",
            "reading_p2_max": "ALTER TABLE term_configs ADD COLUMN reading_p2_max FLOAT",
            "spelling_max": "ALTER TABLE term_configs ADD COLUMN spelling_max FLOAT",
            "grammar_max": "ALTER TABLE term_configs ADD COLUMN grammar_max FLOAT",
        }
        with db.engine.begin() as conn:
            for name, sql in needed.items():
                if name not in cols:
                    conn.execute(text(sql))

    def ensure_audit_columns():
        try:
            pupil_cols = {c["name"] for c in inspect(db.engine).get_columns("pupils")}
            result_cols = {c["name"] for c in inspect(db.engine).get_columns("results")}
            pqs_cols = {c["name"] for c in inspect(db.engine).get_columns("pupil_question_scores")}
        except NoSuchTableError:
            return

        with db.engine.begin() as conn:
            if "updated_at" not in pupil_cols:
                conn.execute(text("ALTER TABLE pupils ADD COLUMN updated_at DATETIME"))
            if "updated_at" not in result_cols:
                conn.execute(text("ALTER TABLE results ADD COLUMN updated_at DATETIME"))
            if "updated_by_teacher_id" not in result_cols:
                conn.execute(text("ALTER TABLE results ADD COLUMN updated_by_teacher_id INTEGER"))
            if "updated_at" not in pqs_cols:
                conn.execute(text("ALTER TABLE pupil_question_scores ADD COLUMN updated_at DATETIME"))
            if "updated_by_teacher_id" not in pqs_cols:
                conn.execute(text("ALTER TABLE pupil_question_scores ADD COLUMN updated_by_teacher_id INTEGER"))

    def ensure_result_columns():
        try:
            cols = {c["name"] for c in inspect(db.engine).get_columns("results")}
        except NoSuchTableError:
            return
        needed = {
            "reading_p1": "ALTER TABLE results ADD COLUMN reading_p1 FLOAT",
            "reading_p2": "ALTER TABLE results ADD COLUMN reading_p2 FLOAT",
            "spelling": "ALTER TABLE results ADD COLUMN spelling FLOAT",
            "grammar": "ALTER TABLE results ADD COLUMN grammar FLOAT",
        }
        with db.engine.begin() as conn:
            for name, sql in needed.items():
                if name not in cols:
                    conn.execute(text(sql))

    def ensure_teacher_admin_columns_and_links():
        """
        Transitional schema helper until proper Alembic migrations are added.
        TODO: replace with migrations in /migrations for production rollouts.
        """
        insp = inspect(db.engine)
        with db.engine.begin() as conn:
            teacher_cols = {c["name"] for c in insp.get_columns("teachers")}
            if "is_active" not in teacher_cols:
                conn.execute(text("ALTER TABLE teachers ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS teacher_classes (
                    teacher_id INTEGER NOT NULL,
                    class_id INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (teacher_id, class_id),
                    FOREIGN KEY(teacher_id) REFERENCES teachers (id),
                    FOREIGN KEY(class_id) REFERENCES classes (id)
                )
            """))

            conn.execute(text("""
                INSERT OR IGNORE INTO teacher_classes (teacher_id, class_id, created_at)
                SELECT id, class_id, CURRENT_TIMESTAMP
                FROM teachers
                WHERE class_id IS NOT NULL
            """))



    def ensure_writing_results_table():
        insp = inspect(db.engine)
        if not insp.has_table("writing_results"):
            WritingResult.__table__.create(bind=db.engine)
            return

        cols = {c["name"] for c in insp.get_columns("writing_results")}
        with db.engine.begin() as conn:
            if "note" not in cols:
                conn.execute(text("ALTER TABLE writing_results ADD COLUMN note TEXT"))
            if "created_at" not in cols:
                conn.execute(text("ALTER TABLE writing_results ADD COLUMN created_at DATETIME"))
                conn.execute(text("UPDATE writing_results SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
            if "band" in cols:
                conn.execute(text("UPDATE writing_results SET band='working_towards' WHERE lower(trim(band)) IN ('working towards', 'working towards are', 'wts')"))
                conn.execute(text("UPDATE writing_results SET band='working_at' WHERE lower(trim(band)) IN ('working at', 'working at are', 'ot')"))
                conn.execute(text("UPDATE writing_results SET band='exceeding' WHERE lower(trim(band)) IN ('exceeding', 'exceeding are', 'gds')"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_writing_results_year_term_pupil ON writing_results (academic_year_id, term, pupil_id)"))

    def ensure_sats_tables():
        with db.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS sats_headers (
                    id INTEGER PRIMARY KEY,
                    class_id INTEGER NOT NULL,
                    academic_year_id INTEGER NOT NULL,
                    key VARCHAR(20) NOT NULL,
                    header VARCHAR(120),
                    "group" VARCHAR(20) NOT NULL,
                    "order" INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(class_id) REFERENCES classes (id),
                    FOREIGN KEY(academic_year_id) REFERENCES academic_years (id),
                    CONSTRAINT uq_sats_header_unique UNIQUE (class_id, academic_year_id, key)
                )
            """))

        try:
            cols = {c["name"] for c in inspect(db.engine).get_columns("sats_scores")}
        except NoSuchTableError:
            return

        with db.engine.begin() as conn:
            if "key" not in cols:
                conn.execute(text("ALTER TABLE sats_scores ADD COLUMN key VARCHAR(20)"))
                if "column_key" in cols:
                    conn.execute(text("UPDATE sats_scores SET key = column_key WHERE key IS NULL OR TRIM(key) = ''"))

    def ensure_pupil_class_history_table():
        insp = inspect(db.engine)
        table_names = set(insp.get_table_names())

        if "pupil_class_history" not in table_names:
            with db.engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE pupil_class_history (
                        id INTEGER PRIMARY KEY,
                        pupil_id INTEGER NOT NULL,
                        class_id INTEGER NOT NULL,
                        academic_year_id INTEGER NOT NULL,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(pupil_id) REFERENCES pupils (id),
                        FOREIGN KEY(class_id) REFERENCES classes (id),
                        FOREIGN KEY(academic_year_id) REFERENCES academic_years (id),
                        CONSTRAINT uq_pupil_class_history UNIQUE (pupil_id, class_id, academic_year_id)
                    )
                """))

        with db.engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pupil_class_history_pupil_id ON pupil_class_history (pupil_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pupil_class_history_class_id ON pupil_class_history (class_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pupil_class_history_academic_year_id ON pupil_class_history (academic_year_id)"))

    def ensure_paper_template_tables_and_columns():
        with db.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS paper_templates (
                    id INTEGER PRIMARY KEY,
                    subject VARCHAR(20) NOT NULL,
                    paper VARCHAR(30) NOT NULL,
                    academic_year_id INTEGER NOT NULL,
                    year_group INTEGER NOT NULL,
                    term VARCHAR(10) NOT NULL,
                    title VARCHAR(160),
                    is_active BOOLEAN NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(academic_year_id) REFERENCES academic_years (id)
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS paper_template_questions (
                    id INTEGER PRIMARY KEY,
                    template_id INTEGER NOT NULL,
                    number INTEGER NOT NULL,
                    max_mark FLOAT NOT NULL DEFAULT 1.0,
                    question_type VARCHAR(120),
                    notes TEXT,
                    strand VARCHAR(120),
                    FOREIGN KEY(template_id) REFERENCES paper_templates (id),
                    CONSTRAINT uq_template_question_number UNIQUE (template_id, number)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_templates_lookup ON paper_templates (subject, paper, academic_year_id, year_group, term, is_active)"))

        def ensure_column(table, column, sql):
            cols = {c["name"] for c in inspect(db.engine).get_columns(table)}
            if column not in cols:
                with db.engine.begin() as conn:
                    conn.execute(text(sql))

        ensure_column("assessment_questions", "question_type", "ALTER TABLE assessment_questions ADD COLUMN question_type VARCHAR(120)")
        ensure_column("assessment_questions", "notes", "ALTER TABLE assessment_questions ADD COLUMN notes TEXT")
        ensure_column("assessments", "template_id", "ALTER TABLE assessments ADD COLUMN template_id INTEGER")
        ensure_column("assessments", "template_version", "ALTER TABLE assessments ADD COLUMN template_version INTEGER")
        ensure_column("interventions", "teacher_note", "ALTER TABLE interventions ADD COLUMN teacher_note TEXT")
        ensure_column("interventions", "teacher_updated_at", "ALTER TABLE interventions ADD COLUMN teacher_updated_at DATETIME")
        ensure_column("interventions", "focus_areas", "ALTER TABLE interventions ADD COLUMN focus_areas TEXT")
        ensure_column("interventions", "pre_result", "ALTER TABLE interventions ADD COLUMN pre_result VARCHAR(120)")
        ensure_column("interventions", "post_result", "ALTER TABLE interventions ADD COLUMN post_result VARCHAR(120)")
        ensure_column("interventions", "review_due_date", "ALTER TABLE interventions ADD COLUMN review_due_date DATE")

    def next_year_label_from(label: str) -> str:
        """Convert labels like '2025/26' into '2026/27'."""
        raw = (label or "").strip()
        m = re.fullmatch(r"(\d{4})\/(\d{2})", raw)
        if m:
            start = int(m.group(1)) + 1
            end = int(m.group(2)) + 1
            return f"{start}/{end:02d}"

        fallback_start = datetime.utcnow().year
        m2 = re.search(r"(\d{4})", raw)
        if m2:
            fallback_start = int(m2.group(1)) + 1
        return f"{fallback_start}/{(fallback_start + 1) % 100:02d}"

    def get_or_create_archive_class():
        return ensure_single_archive_class()

    def live_classes_query():
        return SchoolClass.query.filter(
            SchoolClass.is_archived.is_(False),
            SchoolClass.is_archive.is_(False)
        )

    def active_classes_query():
        return live_classes_query()

    def is_admin_user():
        return bool(getattr(current_user, "is_admin", False))

    def primary_class_id_for(user):
        if not user:
            return None
        if getattr(user, "class_id", None):
            return user.class_id
        if getattr(user, "classes", None):
            first = user.classes[0] if user.classes else None
            return first.id if first else None
        return None

    def user_class_ids(user):
        if not user:
            return []
        ids = set()
        if getattr(user, "class_id", None):
            ids.add(user.class_id)
        for klass in getattr(user, "classes", []) or []:
            ids.add(klass.id)
        return sorted(ids)

    def user_has_class_access(user, class_id):
        if not user or not class_id:
            return False
        if getattr(user, "is_admin", False):
            return True
        return int(class_id) in user_class_ids(user)

    def get_effective_pupil_ids_for(class_id, selected_year_id):
        current_year = get_current_year()

        if selected_year_id and current_year and int(selected_year_id) != int(current_year.id):
            history_ids = [
                r[0] for r in db.session.query(PupilClassHistory.pupil_id)
                .filter_by(class_id=class_id, academic_year_id=selected_year_id)
                .all()
            ]
            if history_ids:
                return history_ids

        return [
            r[0] for r in db.session.query(Pupil.id)
            .filter(Pupil.class_id == class_id)
            .all()
        ]

    def require_pupil_access(pupil):
        if is_admin_user():
            return
        if not user_has_class_access(current_user, pupil.class_id):
            abort(403)

    def require_class_access(class_id):
        if is_admin_user():
            return
        if not user_has_class_access(current_user, class_id):
            abort(403)

    def get_term_max_for_paper(class_id: int, academic_year_id: int, term: str, subject: str, paper: str) -> float:
        subject = normalize_subject(subject)
        cfg = TermConfig.query.filter_by(
            class_id=class_id,
            academic_year_id=academic_year_id,
            term=term,
        ).first()

        if cfg:
            defaults = {
                ("maths", "Arithmetic"): cfg.arith_max,
                ("maths", "Reasoning"): cfg.reason_max,
                ("reading", "Paper 1"): cfg.reading_p1_max,
                ("reading", "Paper 2"): cfg.reading_p2_max,
                ("spag", "Spelling"): cfg.spelling_max,
                ("spag", "Grammar"): cfg.grammar_max,
            }
            value = defaults.get((subject, paper))
            if value is not None:
                return float(value)

        defaults = {
            ("maths", "Arithmetic"): app.config["ARITH_MAX"],
            ("maths", "Reasoning"): app.config["REASON_MAX"],
            ("reading", "Paper 1"): app.config["READING_P1_MAX"],
            ("reading", "Paper 2"): app.config["READING_P2_MAX"],
            ("spag", "Spelling"): app.config["SPAG_SPELLING_MAX"],
            ("spag", "Grammar"): app.config["SPAG_GRAMMAR_MAX"],
        }
        return float(defaults.get((subject, paper), 0.0))

    def result_field_for_paper(subject: str, paper: str) -> str:
        subject = normalize_subject(subject)
        mapping = {
            ("maths", "Arithmetic"): "arithmetic",
            ("maths", "Reasoning"): "reasoning",
            ("reading", "Paper 1"): "reading_p1",
            ("reading", "Paper 2"): "reading_p2",
            ("spag", "Spelling"): "spelling",
            ("spag", "Grammar"): "grammar",
        }
        return mapping.get((subject, paper), "arithmetic")

    def get_result_scores(result: Result, subject: str):
        subject = normalize_subject(subject)
        if subject == "maths":
            return result.arithmetic, result.reasoning
        if subject == "reading":
            return result.reading_p1, result.reading_p2
        return result.spelling, result.grammar

    def set_result_scores(result: Result, subject: str, score_a, score_b):
        subject = normalize_subject(subject)
        if subject == "maths":
            result.arithmetic = score_a
            result.reasoning = score_b
        elif subject == "reading":
            result.reading_p1 = score_a
            result.reading_p2 = score_b
        else:
            result.spelling = score_a
            result.grammar = score_b

    def compute_band_from_pct(pct, subject):
        """Return band label using subject-specific threshold config."""
        subject = normalize_subject(subject)
        thresholds = app.config["BAND_THRESHOLDS"].get(
            subject,
            app.config["BAND_THRESHOLDS"]["maths"],
        )
        if pct < thresholds["wts_max"]:
            return "Working towards ARE"
        if pct < thresholds["ot_max"]:
            return "Working at ARE"
        return "Exceeding ARE"

    def compute_combined_and_band(arith, reason, klass_id, term, year_id, subject="maths"):
        """Compute Combined % and band using subject-aware maxima + thresholds."""
        if arith is None and reason is None:
            return None, None

        arith = arith or 0.0
        reason = reason or 0.0
        subject = normalize_subject(subject)

        paper_a, paper_b = PAPERS[subject]
        total_max = (
            get_term_max_for_paper(klass_id, year_id, term, subject, paper_a)
            + get_term_max_for_paper(klass_id, year_id, term, subject, paper_b)
        )

        combined_pct = round(((arith + reason) / total_max) * 100.0, 1) if total_max > 0 else 0.0
        return combined_pct, compute_band_from_pct(combined_pct, subject)


    SATS_DEFAULT_COLUMNS = (("Maths", "M", 18), ("Reading", "R", 12), ("SPaG", "S", 16))

    def parse_year_id_or_current(raw_year):
        if raw_year:
            try:
                y = AcademicYear.query.get(int(raw_year))
            except (TypeError, ValueError):
                y = None
            if y:
                return y
        return get_current_year()

    def ensure_sats_headers(class_id: int, academic_year_id: int):
        changed = False
        existing = {
            c.key for c in SatsHeader.query.filter_by(
                class_id=class_id,
                academic_year_id=academic_year_id,
            ).all()
        }

        order_counter = 1
        for group_name, key_prefix, count in SATS_DEFAULT_COLUMNS:
            for i in range(1, count + 1):
                key = f"{key_prefix}{i}"
                if key not in existing:
                    db.session.add(SatsHeader(
                        class_id=class_id,
                        academic_year_id=academic_year_id,
                        key=key,
                        header="",
                        group=group_name,
                        order=order_counter,
                    ))
                    changed = True
                order_counter += 1

        if changed:
            db.session.commit()

    def user_can_access_class(klass: SchoolClass) -> bool:
        if not klass:
            return False
        return user_has_class_access(current_user, klass.id)

    def academic_year_choices():
        return [(y.id, y.label) for y in AcademicYear.query.order_by(AcademicYear.label.asc()).all()]

    def class_choices():
        return [(c.id, c.name) for c in active_classes_query().order_by(SchoolClass.name.asc()).all()]

    def band_css(summary: str) -> str:
        """Map standard band labels to CSS classes for colour-coding."""
        s = (summary or "").strip().lower()
        if s == "working towards are" or "towards" in s:
            return "band-wts"    # pale red
        if s == "working at are" or "working at" in s:
            return "band-ot"     # pale green
        if s == "exceeding are" or "exceed" in s:
            return "band-gds"    # pale orange
        return ""

    # ---------- GAP–TermConfig sync helpers ----------

    def paper_title(klass_name: str, subject: str, term: str, paper: str, year_label: str) -> str:
        return f"{klass_name} {subject.upper()} {term} {paper} {year_label}"

    def get_active_template(subject: str, paper: str, year_id: int, year_group: int, term: str):
        return (PaperTemplate.query
                .filter_by(subject=subject, paper=paper, academic_year_id=year_id, year_group=year_group, term=term, is_active=True)
                .order_by(PaperTemplate.version.desc())
                .first())

    def clone_template(source: PaperTemplate, make_active=False):
        new_t = PaperTemplate(
            subject=source.subject,
            paper=source.paper,
            academic_year_id=source.academic_year_id,
            year_group=source.year_group,
            term=source.term,
            title=source.title,
            is_active=make_active,
            version=(source.version or 1) + 1,
        )
        db.session.add(new_t)
        db.session.flush()
        src_qs = PaperTemplateQuestion.query.filter_by(template_id=source.id).order_by(PaperTemplateQuestion.number.asc()).all()
        for q in src_qs:
            db.session.add(PaperTemplateQuestion(
                template_id=new_t.id,
                number=q.number,
                max_mark=q.max_mark,
                question_type=q.question_type,
                notes=q.notes,
                strand=q.strand,
            ))
        if make_active:
            source.is_active = False
        db.session.flush()
        return new_t

    def ensure_assessment_questions_from_template(a: Assessment):
        if not a.template_id:
            return
        template_questions = PaperTemplateQuestion.query.filter_by(template_id=a.template_id).order_by(PaperTemplateQuestion.number.asc()).all()
        if not template_questions:
            return
        AssessmentQuestion.query.filter_by(assessment_id=a.id).delete()
        for q in template_questions:
            db.session.add(AssessmentQuestion(
                assessment_id=a.id,
                number=q.number,
                max_mark=q.max_mark,
                strand=q.strand,
                question_type=q.question_type,
                notes=q.notes,
            ))
        db.session.flush()

    def recompute_focus_areas_for_intervention(itm: Intervention):
        assessment = (Assessment.query
            .filter_by(class_id=itm.class_id, academic_year_id=itm.academic_year_id, term=itm.term, subject='maths', paper=itm.paper)
            .order_by(Assessment.created_at.desc())
            .first())
        if not assessment:
            itm.focus_areas = json.dumps([])
            return []
        questions = AssessmentQuestion.query.filter_by(assessment_id=assessment.id).all()
        q_by_id = {q.id: q for q in questions}
        rows = PupilQuestionScore.query.filter_by(assessment_id=assessment.id, pupil_id=itm.pupil_id).all()
        by_type = {}
        for row in rows:
            q = q_by_id.get(row.question_id)
            if not q:
                continue
            qtype = (q.question_type or q.strand or 'General').strip() or 'General'
            bucket = by_type.setdefault(qtype, {'mark': 0.0, 'max': 0.0})
            bucket['mark'] += float(row.mark or 0.0)
            bucket['max'] += float(q.max_mark or 0.0)
        scored = []
        for name, vals in by_type.items():
            pct = ((vals['mark'] / vals['max']) * 100.0) if vals['max'] > 0 else 0.0
            scored.append((name, round(pct, 1)))
        scored.sort(key=lambda t: t[1])
        focus = [x[0] for x in scored[:3]] or ['General']
        itm.focus_areas = json.dumps(focus)
        return focus

    def sync_assessment_totals_to_results(assessment_id: int):
        a = Assessment.query.get_or_404(assessment_id)
        subject = normalize_subject(a.subject or "maths")
        paper = a.paper or PAPERS[subject][0]
        field = result_field_for_paper(subject, paper)

        questions = (AssessmentQuestion.query
                     .filter_by(assessment_id=a.id)
                     .order_by(AssessmentQuestion.number.asc())
                     .all())
        q_by_id = {q.id: q for q in questions}

        pupils = (Pupil.query
                  .filter_by(class_id=a.class_id)
                  .order_by(Pupil.number.is_(None), Pupil.number, Pupil.name)
                  .all())

        scores = PupilQuestionScore.query.filter_by(assessment_id=a.id).all()

        totals = {p.id: 0.0 for p in pupils}
        for s in scores:
            if s.pupil_id in totals and s.question_id in q_by_id:
                totals[s.pupil_id] += float(s.mark or 0.0)

        for p in pupils:
            total_mark = totals.get(p.id, 0.0)

            r = Result.query.filter_by(
                pupil_id=p.id,
                academic_year_id=a.academic_year_id,
                term=a.term,
                subject=subject,
            ).first()

            if not r:
                r = Result(
                    pupil_id=p.id,
                    academic_year_id=a.academic_year_id,
                    term=a.term,
                    class_id_snapshot=p.class_id,
                    subject=subject,
                )
                db.session.add(r)
                db.session.flush()

            setattr(r, field, total_mark)

            score_a, score_b = get_result_scores(r, subject)
            combined_pct, summary = compute_combined_and_band(
                score_a, score_b, p.class_id, a.term, a.academic_year_id, subject
            )
            r.combined_pct = combined_pct
            r.summary = summary
            r.class_id_snapshot = p.class_id

        db.session.commit()

    def get_or_create_assessment_for(klass_id: int, year_id: int, term: str, subject: str, paper: str):
        klass = SchoolClass.query.get(klass_id)
        year = AcademicYear.query.get(year_id)
        subject = normalize_subject(subject)
        title = paper_title(klass.name, subject, term, paper, year.label)
        template = None
        if klass and klass.year_group:
            template = get_active_template(subject, paper, year_id, klass.year_group, term)

        a = (Assessment.query
             .filter_by(class_id=klass_id, academic_year_id=year_id, term=term, subject=subject, paper=paper)
             .first())
        if not a:
            a = Assessment(
                class_id=klass_id,
                academic_year_id=year_id,
                term=term,
                subject=subject,
                paper=paper,
                title=title,
                template_id=(template.id if template else None),
                template_version=(template.version if template else None),
            )
            db.session.add(a)
            db.session.flush()
        else:
            if not a.title:
                a.title = title
            if template and a.template_id != template.id:
                a.template_id = template.id
                a.template_version = template.version

        if a.template_id:
            ensure_assessment_questions_from_template(a)
        return a

    def ensure_questions_total_marks(assessment_id: int, required_total: float):
        """
        Ensure sum(max_mark) equals required_total by:
        - adding a final question (or increasing the last one) if short,
        - removing questions from the end and trimming the new tail if too many,
        - renumbering 1..N.
        """
        qs = (AssessmentQuestion.query
              .filter_by(assessment_id=assessment_id)
              .order_by(AssessmentQuestion.number.asc())
              .all())

        if not qs:
            whole = int(required_total)
            for i in range(1, whole + 1):
                db.session.add(
                    AssessmentQuestion(assessment_id=assessment_id, number=i, max_mark=1.0)
                )
            remainder = round(required_total - whole, 4)
            if remainder > 0:
                db.session.add(
                    AssessmentQuestion(assessment_id=assessment_id, number=whole + 1, max_mark=remainder)
                )
            db.session.flush()
            return

        total = sum(float(q.max_mark or 0.0) for q in qs)

        if total < required_total:
            remaining = round(required_total - total, 4)
            if remaining <= 1.0:
                qs[-1].max_mark = round(float(qs[-1].max_mark or 0.0) + remaining, 4)
            else:
                last_no = qs[-1].number if qs else 0
                while remaining > 1.0:
                    db.session.add(
                        AssessmentQuestion(assessment_id=assessment_id, number=last_no + 1, max_mark=1.0)
                    )
                    last_no += 1
                    remaining = round(remaining - 1.0, 4)
                if remaining > 0:
                    db.session.add(
                        AssessmentQuestion(assessment_id=assessment_id, number=last_no + 1, max_mark=remaining)
                    )
            db.session.flush()

        elif total > required_total:
            qs = (AssessmentQuestion.query
                  .filter_by(assessment_id=assessment_id)
                  .order_by(AssessmentQuestion.number.asc())
                  .all())
            idx = len(qs) - 1
            running = total
            while idx >= 0 and running > required_total:
                q = qs[idx]
                q_mark = float(q.max_mark or 0.0)
                if running - q_mark >= required_total:
                    running = round(running - q_mark, 4)
                    db.session.delete(q)
                    idx -= 1
                else:
                    q.max_mark = round(required_total - (running - q_mark), 4)
                    break
            db.session.flush()

        # Renumber
        qs2 = (AssessmentQuestion.query
               .filter_by(assessment_id=assessment_id)
               .order_by(AssessmentQuestion.number.asc())
               .all())
        for i, q in enumerate(qs2, start=1):
            q.number = i
        db.session.flush()

    def sync_gap_assessments_for_class_year(klass_id: int, year_id: int):
        """Keep subject-specific GAP assessments aligned with configured maxima."""
        for term in TERMS:
            for subject in SUBJECTS:
                for paper in PAPERS[subject]:
                    a = get_or_create_assessment_for(klass_id, year_id, term, subject, paper)
                    required_total = get_term_max_for_paper(klass_id, year_id, term, subject, paper)
                    ensure_questions_total_marks(a.id, required_total)
        db.session.commit()

    @app.context_processor
    def inject_sats_nav():
        if not getattr(current_user, "is_authenticated", False):
            return {"sats_nav": None, "y6_nav": None}

        year = parse_year_id_or_current(request.args.get("year"))
        class_sel = request.args.get("class")
        sats_nav = None

        if getattr(current_user, "is_admin", False):
            klass = None
            if class_sel not in (None, "", "all"):
                try:
                    klass = SchoolClass.query.get(int(class_sel))
                except (TypeError, ValueError):
                    klass = None
        else:
            klass = SchoolClass.query.get(primary_class_id_for(current_user)) if primary_class_id_for(current_user) else None

        if klass and (not klass.is_archived) and (not klass.is_archive) and klass.year_group == 6:
            sats_nav = {"class_id": klass.id, "year_id": (year.id if year else None)}

        return {"sats_nav": sats_nav, "y6_nav": sats_nav}


    WRITING_BANDS = ("working_towards", "working_at", "exceeding")

    def writing_band_css(band: str) -> str:
        if band == "working_towards":
            return "band-wts"
        if band == "working_at":
            return "band-ot"
        if band == "exceeding":
            return "band-gds"
        return ""

    def writing_band_label(band: str) -> str:
        labels = {
            "working_towards": "Working towards",
            "working_at": "Working at",
            "exceeding": "Exceeding",
        }
        return labels.get(band, "")

    def parse_bool_filter(value):
        if value == "1":
            return True
        if value == "0":
            return False
        return None

    def current_outcomes_for_pupils(pupil_ids, year_id):
        if not pupil_ids:
            return {}
        term_rank = case(
            (Result.term == "Autumn", 1),
            (Result.term == "Spring", 2),
            (Result.term == "Summer", 3),
            else_=0,
        )

        outcomes = {pid: {"reading": None, "maths": None, "writing": None} for pid in pupil_ids}

        non_writing = (
            Result.query
            .filter(Result.pupil_id.in_(pupil_ids), Result.academic_year_id == year_id, Result.subject.in_(["reading", "maths"]))
            .order_by(Result.pupil_id.asc(), Result.subject.asc(), term_rank.desc(), Result.updated_at.desc(), Result.created_at.desc())
            .all()
        )
        seen = set()
        for row in non_writing:
            key = (row.pupil_id, row.subject)
            if key in seen:
                continue
            seen.add(key)
            outcomes[row.pupil_id][row.subject] = row.summary

        writing_rank = case(
            (WritingResult.term == "Autumn", 1),
            (WritingResult.term == "Spring", 2),
            (WritingResult.term == "Summer", 3),
            else_=0,
        )
        writing_rows = (
            WritingResult.query
            .filter(WritingResult.pupil_id.in_(pupil_ids), WritingResult.academic_year_id == year_id)
            .order_by(WritingResult.pupil_id.asc(), writing_rank.desc(), WritingResult.created_at.desc())
            .all()
        )
        writing_seen = set()
        for row in writing_rows:
            if row.pupil_id in writing_seen:
                continue
            writing_seen.add(row.pupil_id)
            outcomes[row.pupil_id]["writing"] = writing_band_label(row.band)
        return outcomes

    def band_class_from_text(value):
        text_value = (value or "").strip().lower()
        if not text_value:
            return ""
        if "toward" in text_value or "wts" in text_value:
            return "band-wts"
        if "exceed" in text_value or "greater" in text_value or "gds" in text_value:
            return "band-gds"
        return "band-ot"

    def get_or_create_pupil_profile(pupil_id: int):
        profile = PupilProfile.query.filter_by(pupil_id=pupil_id).first()
        if profile:
            return profile
        profile = PupilProfile(pupil_id=pupil_id)
        db.session.add(profile)
        db.session.flush()
        return profile

    def apply_group_filters(q, gender="", pp="", laps="", svc=""):
        if gender in ("F", "M"):
            q = q.filter(Pupil.gender == gender)

        def tri(qs, field, val):
            if val == "1":
                return qs.filter(field.is_(True))
            if val == "0":
                return qs.filter(field.is_(False))
            return qs

        q = tri(q, Pupil.pupil_premium, pp)
        q = tri(q, Pupil.laps, laps)
        q = tri(q, Pupil.service_child, svc)
        return q

    def parse_cohort_filters(args):
        gender = (args.get("gender") or "").upper()
        pp = args.get("pp", "")
        laps = args.get("laps", "")
        svc = args.get("svc", "")
        group = (args.get("group") or "all").strip().lower()

        group_map = {
            "all": {"gender": "", "pp": "", "laps": "", "svc": ""},
            "boys": {"gender": "M", "pp": "", "laps": "", "svc": ""},
            "girls": {"gender": "F", "pp": "", "laps": "", "svc": ""},
            "pp": {"gender": "", "pp": "1", "laps": "", "svc": ""},
            "non_pp": {"gender": "", "pp": "0", "laps": "", "svc": ""},
            "laps": {"gender": "", "pp": "", "laps": "1", "svc": ""},
            "service": {"gender": "", "pp": "", "laps": "", "svc": "1"},
        }
        if group in group_map and group != "all":
            selected = group_map[group]
            gender = selected["gender"]
            pp = selected["pp"]
            laps = selected["laps"]
            svc = selected["svc"]
        else:
            group = "all"

        return {
            "group": group,
            "gender": gender,
            "pp": pp,
            "laps": laps,
            "svc": svc,
        }

    def sats_score_class(score):
        if score is None:
            return "score-na"
        try:
            return "score-good" if float(score) >= 100 else "score-low"
        except (TypeError, ValueError):
            return "score-na"

    def normalize_sats_score(value):
        if value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if value.is_integer():
            return int(value)
        return round(value, 1)

    def latest_sats_scaled_by_pupil(pupil_ids, academic_year_id, subject):
        if not pupil_ids or not academic_year_id or subject not in {"maths", "reading", "spag"}:
            return {}

        base = "M" if subject == "maths" else ("R" if subject == "reading" else "S")
        scaled_keys = [f"{base}{i}" for i in range(5, 9)]
        score_rows = (SatsScore.query
                      .filter(
                          SatsScore.pupil_id.in_(pupil_ids),
                          SatsScore.academic_year_id == academic_year_id,
                          SatsScore.key.in_(scaled_keys),
                      )
                      .all())
        score_map = {(row.pupil_id, row.key): row.value for row in score_rows}

        latest = {}
        for pupil_id in pupil_ids:
            values = [normalize_sats_score(score_map.get((pupil_id, key))) for key in scaled_keys]
            latest[pupil_id] = next((score for score in reversed(values) if score is not None), None)
        return latest

    def build_year6_home_rows(class_id, academic_year_id, filter_params):
        if not class_id or not academic_year_id:
            return []

        gender = (filter_params.get("gender") or "all").strip().lower()
        pp = (filter_params.get("pp") or "all").strip().lower()
        laps = (filter_params.get("laps") or "all").strip().lower()
        service = (filter_params.get("service") or "all").strip().lower()

        pupils_q = (Pupil.query
                    .filter(Pupil.class_id == class_id)
                    .order_by(Pupil.number.is_(None), Pupil.number.asc(), Pupil.name.asc()))

        if gender == "male":
            pupils_q = pupils_q.filter(Pupil.gender == "M")
        elif gender == "female":
            pupils_q = pupils_q.filter(Pupil.gender == "F")

        if pp == "pp":
            pupils_q = pupils_q.filter(Pupil.pupil_premium.is_(True))
        elif pp == "nonpp":
            pupils_q = pupils_q.filter(Pupil.pupil_premium.is_(False))

        if laps == "laps":
            pupils_q = pupils_q.filter(Pupil.laps.is_(True))
        elif laps == "nonlaps":
            pupils_q = pupils_q.filter(Pupil.laps.is_(False))

        if service == "service":
            pupils_q = pupils_q.filter(Pupil.service_child.is_(True))
        elif service == "nonservice":
            pupils_q = pupils_q.filter(Pupil.service_child.is_(False))

        pupils = pupils_q.all()
        if not pupils:
            return []

        pupil_ids = [p.id for p in pupils]
        subject_keys = {
            "maths": {"raw": [f"M{i}" for i in range(1, 5)], "scaled": [f"M{i}" for i in range(5, 9)]},
            "reading": {"raw": [f"R{i}" for i in range(1, 5)], "scaled": [f"R{i}" for i in range(5, 9)]},
            "spag": {"raw": [f"S{i}" for i in range(1, 5)], "scaled": [f"S{i}" for i in range(5, 9)]},
        }
        all_keys = [k for cfg in subject_keys.values() for grp in cfg.values() for k in grp]

        score_rows = (SatsScore.query
                      .filter(
                          SatsScore.pupil_id.in_(pupil_ids),
                          SatsScore.academic_year_id == academic_year_id,
                          SatsScore.key.in_(all_keys),
                      )
                      .all())
        score_map = {(row.pupil_id, row.key): row.value for row in score_rows}

        latest_scaled_by_subject = {
            subject_name: latest_sats_scaled_by_pupil(pupil_ids, academic_year_id, subject_name)
            for subject_name in ("maths", "reading", "spag")
        }

        def pick_scores(pupil_id, keys):
            values = [score_map.get((pupil_id, key)) for key in keys]
            return [normalize_sats_score(value) for value in values]

        rows = []
        for pupil in pupils:
            row = {
                "pupil_id": pupil.id,
                "name": pupil.name,
                "badges": {
                    "PP": bool(pupil.pupil_premium),
                    "LAP": bool(pupil.laps),
                    "SC": bool(pupil.service_child),
                    "gender": (pupil.gender or ""),
                },
            }
            for subject_name, keys in subject_keys.items():
                raw_scores = pick_scores(pupil.id, keys["raw"])
                scaled_scores = pick_scores(pupil.id, keys["scaled"])
                latest_scaled = latest_scaled_by_subject[subject_name].get(pupil.id)
                row[subject_name] = {
                    "raw": raw_scores,
                    "scaled": scaled_scores,
                    "latest_scaled": latest_scaled,
                }
            rows.append(row)

        return rows

    def year6_sats_overview(rows):
        overview = {
            "maths": {"under": 0, "at": 0, "above": 0, "missing": 0, "total": 0},
            "reading": {"under": 0, "at": 0, "above": 0, "missing": 0, "total": 0},
            "spag": {"under": 0, "at": 0, "above": 0, "missing": 0, "total": 0},
        }

        for row in rows:
            for subject in ("maths", "reading", "spag"):
                bucket = overview[subject]
                latest_scaled = row[subject]["latest_scaled"]
                bucket["total"] += 1
                if latest_scaled is None:
                    bucket["missing"] += 1
                elif latest_scaled < 100:
                    bucket["under"] += 1
                elif latest_scaled <= 110:
                    bucket["at"] += 1
                else:
                    bucket["above"] += 1

        return overview

    YEAR6_SUBJECT_KEY_MAP = {
        "maths": [f"M{i}" for i in range(1, 9)],
        "reading": [f"R{i}" for i in range(1, 9)],
        "spag": [f"S{i}" for i in range(1, 9)],
    }

    def parse_year6_cell_key(cell_key: str):
        match = re.fullmatch(r"(maths|reading|spag)_(\d)", (cell_key or "").strip().lower())
        if not match:
            return None
        subject = match.group(1)
        slot = int(match.group(2))
        if slot < 1 or slot > 8:
            return None
        return YEAR6_SUBJECT_KEY_MAP[subject][slot - 1]

    def summarize_band(value: str, subject: str):
        val = (value or "").strip().lower()
        if subject == "writing":
            if val == "working_towards":
                return "wts"
            if val == "working_at":
                return "ot"
            if val == "exceeding":
                return "gds"
            return None

        if "towards" in val:
            return "wts"
        if "working at" in val or "on track" in val or val == "ot":
            return "ot"
        if "exceed" in val or "gds" in val or "greater" in val:
            return "gds"
        return None

    def subject_distribution_for_pupil_ids(pupil_ids, year_id, term, subject):
        out = {"wts_count": 0, "ot_count": 0, "gds_count": 0, "total_count": 0, "on_track_count": 0,
               "wts": 0.0, "ot": 0.0, "gds": 0.0, "on_track": 0.0}
        if not pupil_ids:
            return out

        values = []
        if subject in {"maths", "reading", "spag"}:
            year6_ids = [pid for (pid,) in (Pupil.query
                                            .join(SchoolClass, SchoolClass.id == Pupil.class_id)
                                            .filter(Pupil.id.in_(pupil_ids), SchoolClass.year_group == 6)
                                            .with_entities(Pupil.id)
                                            .all())]
            year6_id_set = set(year6_ids)
            non_year6_ids = [pid for pid in pupil_ids if pid not in year6_id_set]

            if year6_ids:
                latest_scaled = latest_sats_scaled_by_pupil(year6_ids, year_id, subject)
                for score in latest_scaled.values():
                    if score is None:
                        continue
                    if score < 100:
                        values.append("wts")
                    elif score <= 110:
                        values.append("ot")
                    else:
                        values.append("gds")

            if non_year6_ids and term in TERMS:
                rows = (Result.query
                        .filter(Result.pupil_id.in_(non_year6_ids), Result.academic_year_id == year_id, Result.term == term, Result.subject == subject)
                        .order_by(Result.created_at.desc())
                        .all())
                latest = {}
                for row in rows:
                    if row.pupil_id not in latest:
                        latest[row.pupil_id] = row
                values.extend([summarize_band(row.summary, subject) for row in latest.values()])

        elif subject == "writing":
            if term not in TERMS:
                return out
            rows = (WritingResult.query
                    .filter(WritingResult.pupil_id.in_(pupil_ids), WritingResult.academic_year_id == year_id, WritingResult.term == term)
                    .order_by(WritingResult.created_at.desc())
                    .all())
            latest = {}
            for row in rows:
                if row.pupil_id not in latest:
                    latest[row.pupil_id] = row
            values = [summarize_band(row.band, "writing") for row in latest.values()]
        else:
            return out

        for value in values:
            if value == "wts":
                out["wts_count"] += 1
            elif value == "ot":
                out["ot_count"] += 1
            elif value == "gds":
                out["gds_count"] += 1

        out["total_count"] = out["wts_count"] + out["ot_count"] + out["gds_count"]
        out["on_track_count"] = out["ot_count"] + out["gds_count"]
        if out["total_count"]:
            total = float(out["total_count"])
            out["wts"] = round(out["wts_count"] / total * 100.0, 1)
            out["ot"] = round(out["ot_count"] / total * 100.0, 1)
            out["gds"] = round(out["gds_count"] / total * 100.0, 1)
            out["on_track"] = round(out["on_track_count"] / total * 100.0, 1)
        return out

    def parse_id_csv(value):
        if not value:
            return []
        out = []
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(int(part))
            except ValueError:
                continue
        return sorted(set(out))

    def scoped_pupil_query(user, year_id=None, class_id=None):
        is_admin = bool(getattr(user, "is_admin", False))
        if is_admin:
            q = Pupil.query.join(SchoolClass, SchoolClass.id == Pupil.class_id).filter(
                SchoolClass.is_archived.is_(False),
                SchoolClass.is_archive.is_(False),
            )
            if class_id:
                q = q.filter(Pupil.class_id == class_id)
            return q

        allowed = user_class_ids(user)
        if not allowed:
            return Pupil.query.filter(Pupil.id == -1)
        if class_id and class_id in allowed:
            return Pupil.query.filter(Pupil.class_id == class_id)
        return Pupil.query.filter(Pupil.class_id.in_(allowed))

    def summarize_row_band(row, subject_key):
        if subject_key == "writing":
            return summarize_band(getattr(row, "band", None), "writing")
        return summarize_band(getattr(row, "summary", None), subject_key)

    def latest_rows_for_subject(pupil_ids, year_id, term, subject_key):
        if not pupil_ids or term not in TERMS:
            return {}
        if subject_key == "writing":
            rows = (WritingResult.query
                    .filter(WritingResult.pupil_id.in_(pupil_ids), WritingResult.academic_year_id == year_id, WritingResult.term == term)
                    .order_by(WritingResult.created_at.desc())
                    .all())
        else:
            rows = (Result.query
                    .filter(Result.pupil_id.in_(pupil_ids), Result.academic_year_id == year_id, Result.term == term, Result.subject == subject_key)
                    .order_by(Result.created_at.desc())
                    .all())
        latest = {}
        for row in rows:
            if row.pupil_id not in latest:
                latest[row.pupil_id] = row
        return latest

    def build_action_needed(user, current_year_id, term_id):
        class_id = request.args.get("class")
        try:
            class_id = int(class_id) if class_id not in (None, "", "all") else None
        except ValueError:
            class_id = None
        subject = (request.args.get("subject") or "maths").strip().lower()
        subject = "writing" if subject == "writing" else (subject if subject in SUBJECTS else "maths")

        pupil_ids = [pid for (pid,) in scoped_pupil_query(user, current_year_id, class_id).with_entities(Pupil.id).all()]
        alerts = []
        if not pupil_ids:
            return alerts

        latest = latest_rows_for_subject(pupil_ids, current_year_id, term_id, subject)
        missing_ids = [pid for pid in pupil_ids if pid not in latest]
        if missing_ids:
            alerts.append({
                "severity": "red",
                "title": f"Missing {subject_display(subject) if subject != 'writing' else 'Writing'} data ({term_id})",
                "count": len(missing_ids),
                "link": url_for("dashboard", subject=subject, mode="table", year=current_year_id, term=term_id, pupil_ids=",".join(str(x) for x in missing_ids)),
            })

        below_ids = [pid for pid, row in latest.items() if summarize_row_band(row, subject) == "wts"]
        if below_ids:
            alerts.append({
                "severity": "amber",
                "title": f"Below expected / not on track ({term_id})",
                "count": len(below_ids),
                "link": url_for("dashboard", subject=subject, mode="table", year=current_year_id, term=term_id, band="wts", pupil_ids=",".join(str(x) for x in below_ids)),
            })

        closed_missing = (Intervention.query
                          .filter(
                              Intervention.academic_year_id == current_year_id,
                              Intervention.status == "closed",
                              Intervention.pupil_id.in_(pupil_ids),
                              ((Intervention.post_result.is_(None)) | (Intervention.post_result == "")),
                          )
                          .all())
        if closed_missing:
            ids = sorted({it.pupil_id for it in closed_missing})
            alerts.append({
                "severity": "red",
                "title": "Closed interventions awaiting post score",
                "count": len(closed_missing),
                "link": url_for("interventions", year=current_year_id, status="awaiting-post", pupil_ids=",".join(str(x) for x in ids)),
            })

        if subject != "writing":
            writing_latest = latest_rows_for_subject(pupil_ids, current_year_id, term_id, "writing")
            writing_missing_ids = [pid for pid in pupil_ids if pid not in writing_latest]
            if writing_missing_ids:
                alerts.append({
                    "severity": "amber",
                    "title": f"Writing judgements missing ({term_id})",
                    "count": len(writing_missing_ids),
                    "link": url_for("dashboard", subject="writing", mode="table", year=current_year_id, term=term_id, pupil_ids=",".join(str(x) for x in writing_missing_ids)),
                })

        return alerts

    def dashboard_summary_payload(year_id, term, subject, group, class_id=None):
        pupil_q = scoped_pupil_query(current_user, year_id, class_id)
        if group == "boys":
            pupil_q = pupil_q.filter(Pupil.gender == "M")
        elif group == "girls":
            pupil_q = pupil_q.filter(Pupil.gender == "F")
        elif group == "pp":
            pupil_q = pupil_q.filter(Pupil.pupil_premium.is_(True))
        elif group == "non_pp":
            pupil_q = pupil_q.filter(Pupil.pupil_premium.is_(False))
        elif group == "laps":
            pupil_q = pupil_q.filter(Pupil.laps.is_(True))
        elif group == "service":
            pupil_q = pupil_q.filter(Pupil.service_child.is_(True))

        pupils = pupil_q.all()
        pupil_ids = [p.id for p in pupils]
        bands = {"WT": 0, "WA": 0, "WAplus": 0}
        on_track = {"yes": 0, "no": 0}

        def consume(subject_key, ids):
            latest = latest_rows_for_subject(ids, year_id, term, subject_key)
            counts = {"WT": 0, "WA": 0, "WAplus": 0}
            for row in latest.values():
                b = summarize_row_band(row, subject_key)
                if b == "wts":
                    counts["WT"] += 1
                elif b == "gds":
                    counts["WAplus"] += 1
                elif b == "ot":
                    counts["WA"] += 1
            return counts

        subject_key = subject if subject in (*SUBJECTS, "writing", "all") else "maths"
        if subject_key == "all":
            all_ids = pupil_ids
            latest_summary = {"WT": 0, "WA": 0, "WAplus": 0}
            for s_key in (*SUBJECTS, "writing"):
                c = consume(s_key, all_ids)
                for k in latest_summary:
                    latest_summary[k] += c[k]
            bands.update(latest_summary)
        else:
            bands.update(consume(subject_key, pupil_ids))

        total = bands["WT"] + bands["WA"] + bands["WAplus"]
        on_track["yes"] = bands["WA"] + bands["WAplus"]
        on_track["no"] = bands["WT"]

        pp_ids = [p.id for p in pupils if p.pupil_premium]
        non_pp_ids = [p.id for p in pupils if not p.pupil_premium]
        pp_counts = consume(subject_key if subject_key != "all" else "maths", pp_ids)
        non_counts = consume(subject_key if subject_key != "all" else "maths", non_pp_ids)

        def wa_plus_pct(c):
            denom = c["WT"] + c["WA"] + c["WAplus"]
            if not denom:
                return 0.0
            return round(((c["WA"] + c["WAplus"]) / denom) * 100.0, 1)

        pp_gap = {
            "pp": wa_plus_pct(pp_counts),
            "non_pp": wa_plus_pct(non_counts),
        }
        pp_gap["gap"] = round(pp_gap["non_pp"] - pp_gap["pp"], 1)

        return {
            "bands": bands,
            "on_track": on_track,
            "pp_compare": {"pp": pp_counts, "non_pp": non_counts},
            "pp_gap": pp_gap,
        }

    def build_report_dataset(args, user):
        subject_raw = (args.get("subject") or "maths").strip().lower()
        subject = "writing" if subject_raw == "writing" else (subject_raw if subject_raw in SUBJECTS else "maths")

        year = parse_year_id_or_current(args.get("year"))
        if not year:
            year = get_current_year()

        is_admin = bool(getattr(user, "is_admin", False))
        class_sel = args.get("class")

        classes = active_classes_query().order_by(SchoolClass.name.asc()).all()
        allowed_class_ids = user_class_ids(user)

        selected_class_id = None
        selected_class = None
        all_classes = False

        if is_admin:
            if class_sel in (None, "", "all"):
                all_classes = True
                selected_class_id = "all"
            else:
                try:
                    selected_class_id = int(class_sel)
                except (TypeError, ValueError):
                    selected_class_id = "all"
                    all_classes = True
                if selected_class_id != "all":
                    selected_class = SchoolClass.query.get(selected_class_id)
                    if not selected_class or selected_class.is_archived or selected_class.is_archive:
                        selected_class = None
                        selected_class_id = "all"
                        all_classes = True
        else:
            forced_class_id = primary_class_id_for(user)
            if not forced_class_id and allowed_class_ids:
                forced_class_id = allowed_class_ids[0]
            if not forced_class_id:
                return {
                    "subject": subject,
                    "year": year,
                    "classes": classes,
                    "selected_class_id": None,
                    "selected_class": None,
                    "headers": [],
                    "rows": [],
                    "filters": {},
                    "filter_summary": "No class assigned.",
                    "show_class_column": False,
                }
            selected_class_id = forced_class_id
            selected_class = SchoolClass.query.get(forced_class_id)
            if not selected_class or selected_class.is_archived or selected_class.is_archive:
                return {
                    "subject": subject,
                    "year": year,
                    "classes": classes,
                    "selected_class_id": None,
                    "selected_class": None,
                    "headers": [],
                    "rows": [],
                    "filters": {},
                    "filter_summary": "No active class assigned.",
                    "show_class_column": False,
                }

        if all_classes:
            q = Pupil.query.join(SchoolClass).filter(
                SchoolClass.is_archived.is_(False),
                SchoolClass.is_archive.is_(False),
            )
        else:
            base_ids = get_effective_pupil_ids_for(int(selected_class_id), year.id)
            if base_ids:
                q = Pupil.query.filter(Pupil.id.in_(base_ids))
            else:
                q = Pupil.query.filter(Pupil.class_id == int(selected_class_id))

        gender = (args.get("gender") or "").upper()
        pp = args.get("pp", "")
        laps = args.get("laps", "")
        svc = args.get("svc", "")
        min_pct = args.get("min_pct", "")
        max_pct = args.get("max_pct", "")
        band = (args.get("band") or "").lower()

        filters = {
            "gender": gender,
            "pp": pp,
            "laps": laps,
            "svc": svc,
            "min_pct": min_pct,
            "max_pct": max_pct,
            "band": band,
        }

        if gender in ("F", "M"):
            q = q.filter(Pupil.gender == gender)

        def tri(qs, field, val):
            if val == "1":
                return qs.filter(field.is_(True))
            if val == "0":
                return qs.filter(field.is_(False))
            return qs

        q = tri(q, Pupil.pupil_premium, pp)
        q = tri(q, Pupil.laps, laps)
        q = tri(q, Pupil.service_child, svc)

        pupils = q.order_by(Pupil.number.is_(None), Pupil.number, Pupil.name).all()
        pupil_ids = [p.id for p in pupils]

        results_by_pupil = {pid: {} for pid in pupil_ids}
        writing_by_pupil = {pid: {} for pid in pupil_ids}

        if pupil_ids:
            if subject == "writing":
                rows = (WritingResult.query
                        .filter(WritingResult.pupil_id.in_(pupil_ids), WritingResult.academic_year_id == year.id)
                        .order_by(WritingResult.created_at.desc())
                        .all())
                for r in rows:
                    if r.term in TERMS and r.term not in writing_by_pupil[r.pupil_id]:
                        writing_by_pupil[r.pupil_id][r.term] = r
            else:
                rows = (Result.query
                        .filter(Result.pupil_id.in_(pupil_ids), Result.academic_year_id == year.id, Result.subject == subject)
                        .order_by(Result.created_at.desc())
                        .all())
                for r in rows:
                    if r.term in TERMS and r.term not in results_by_pupil[r.pupil_id]:
                        results_by_pupil[r.pupil_id][r.term] = r

        def value_matches_filters(result_obj):
            if subject == "writing":
                if band:
                    key = {"wts": "working_towards", "ot": "working_at", "gds": "exceeding"}.get(band)
                    if key and result_obj and result_obj.band != key:
                        return False
                return True

            pct = result_obj.combined_pct if result_obj else None
            if min_pct not in (None, ""):
                try:
                    if pct is None or pct < float(min_pct):
                        return False
                except ValueError:
                    pass
            if max_pct not in (None, ""):
                try:
                    if pct is None or pct > float(max_pct):
                        return False
                except ValueError:
                    pass

            if band and result_obj:
                s = (result_obj.summary or "").lower()
                if band == "wts" and "towards" not in s:
                    return False
                if band == "ot" and "working at" not in s:
                    return False
                if band == "gds" and "exceed" not in s:
                    return False
            elif band and not result_obj:
                return False
            return True

        show_class_column = bool(is_admin and all_classes)
        headers = ["No.", "Name"]
        if show_class_column:
            headers.append("Class")
        headers.extend(["Gender", "PP", "LAPS", "Service", "Autumn", "Spring", "Summer"])

        table_rows = []
        for p in pupils:
            term_values = []
            include = True
            for t in TERMS:
                if subject == "writing":
                    wr = writing_by_pupil.get(p.id, {}).get(t)
                    cell = writing_band_label(wr.band) if wr else ""
                    if not value_matches_filters(wr):
                        include = False
                    term_values.append(cell)
                else:
                    rr = results_by_pupil.get(p.id, {}).get(t)
                    if rr:
                        cell = f"{rr.combined_pct if rr.combined_pct is not None else ''}% ({rr.summary or ''})"
                    else:
                        cell = ""
                    if not value_matches_filters(rr):
                        include = False
                    term_values.append(cell)
            if not include:
                continue

            row = {
                "No.": p.number or "",
                "Name": p.name,
                "Gender": p.gender or "",
                "PP": "Yes" if p.pupil_premium else "No",
                "LAPS": "Yes" if p.laps else "No",
                "Service": "Yes" if p.service_child else "No",
                "Autumn": term_values[0],
                "Spring": term_values[1],
                "Summer": term_values[2],
            }
            if show_class_column:
                row["Class"] = p.klass.name if p.klass else ""
            table_rows.append(row)

        pretty_filters = []
        if gender in ("F", "M"):
            pretty_filters.append(f"Gender={gender}")
        for key, label in ((pp, "PP"), (laps, "LAPS"), (svc, "Service")):
            if key == "1":
                pretty_filters.append(f"{label}=Yes")
            elif key == "0":
                pretty_filters.append(f"{label}=No")
        if min_pct not in (None, ""):
            pretty_filters.append(f"Min %={min_pct}")
        if max_pct not in (None, ""):
            pretty_filters.append(f"Max %={max_pct}")
        if band:
            pretty_filters.append(f"Band={band.upper()}")

        return {
            "subject": subject,
            "subject_label": "Writing" if subject == "writing" else subject_display(subject),
            "year": year,
            "classes": classes,
            "selected_class_id": selected_class_id,
            "selected_class": selected_class,
            "headers": headers,
            "rows": table_rows,
            "filters": filters,
            "filter_summary": ", ".join(pretty_filters) if pretty_filters else "No filters applied",
            "show_class_column": show_class_column,
            "is_admin": is_admin,
            "all_classes": all_classes,
        }

    # ---- Routes

    def ensure_pupil_profiles_table():
        with db.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS pupil_profiles (
                    id INTEGER PRIMARY KEY,
                    pupil_id INTEGER NOT NULL UNIQUE,
                    year_group INTEGER,
                    lac_pla BOOLEAN NOT NULL DEFAULT 0,
                    send BOOLEAN NOT NULL DEFAULT 0,
                    ehcp BOOLEAN NOT NULL DEFAULT 0,
                    vulnerable BOOLEAN NOT NULL DEFAULT 0,
                    attendance_spring1 FLOAT,
                    eyfs_gld BOOLEAN,
                    y1_phonics INTEGER,
                    y2_phonics_retake INTEGER,
                    y2_reading VARCHAR(30),
                    y2_writing VARCHAR(30),
                    y2_maths VARCHAR(30),
                    enrichment TEXT,
                    interventions_note TEXT,
                    FOREIGN KEY(pupil_id) REFERENCES pupils (id)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pupil_profiles_pupil_id ON pupil_profiles (pupil_id)"))

    def ensure_pupil_report_notes_table():
        with db.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS pupil_report_notes (
                    id INTEGER PRIMARY KEY,
                    pupil_id INTEGER NOT NULL,
                    year_id INTEGER NOT NULL,
                    term_id VARCHAR(10) NOT NULL,
                    strengths_text TEXT,
                    next_steps_text TEXT,
                    updated_by INTEGER,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(pupil_id) REFERENCES pupils (id),
                    FOREIGN KEY(year_id) REFERENCES academic_years (id),
                    FOREIGN KEY(updated_by) REFERENCES teachers (id),
                    CONSTRAINT uq_pupil_report_note UNIQUE (pupil_id, year_id, term_id)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pupil_report_notes_lookup ON pupil_report_notes (pupil_id, year_id, term_id)"))

    with app.app_context():
        ensure_archive_column()
        ensure_audit_columns()
        ensure_result_subject_column()
        ensure_assessment_subject_paper_columns()
        ensure_termconfig_columns()
        ensure_result_columns()
        ensure_teacher_admin_columns_and_links()
        ensure_sats_tables()
        ensure_writing_results_table()
        ensure_pupil_class_history_table()
        ensure_paper_template_tables_and_columns()
        ensure_pupil_profiles_table()
        ensure_pupil_report_notes_table()
        ensure_default_year()
        get_or_create_archive_class()

    @app.route("/")
    def index():
        ensure_default_year()
        if current_user.is_authenticated:
            if getattr(current_user, "is_admin", False):
                return redirect(url_for("admin_home"))
            return redirect(url_for("dashboard", subject="maths", mode="home"))

        classes = active_classes_query().order_by(SchoolClass.name).all()
        return render_template(
            "index.html",
            classes=classes,
            is_admin=False,
            klass=None,
            selected_class_id=None,
            pupils=[],
            results_by_pupil={},
            kpi=None,
            years=[],
            selected_year_id=None,
            filters={"gender": "", "pp": "", "laps": "", "svc": ""},
            gap_links=None,
            subject="maths",
            subject_label=subject_display("maths"),
            subjects=SUBJECTS,
            paper_labels=paper_labels_for("maths"),
        )

    @app.route("/dashboard/<subject>")
    @login_required
    def dashboard(subject):
        """
        Dashboard.
        - Term columns chunked into Arithmetic, Reasoning, Combined.
        - GAP links under each term for the selected class & year (with red flag).
        """
        ensure_default_year()
        raw_subject = (subject or "maths").strip().lower()
        subject = "writing" if raw_subject == "writing" else normalize_subject(raw_subject)

        is_admin = bool(getattr(current_user, "is_admin", False))
        mode = (request.args.get("mode") or "").strip().lower()
        if mode not in {"home", "table"}:
            mode = "table" if is_admin else "home"
        requested_subject = (request.args.get("subject") or "").strip().lower()
        if requested_subject:
            if requested_subject in (*SUBJECTS, "writing") and requested_subject != subject:
                args = request.args.to_dict()
                args.pop("subject", None)
                return redirect(url_for("dashboard", subject=requested_subject, **args))
        if is_admin and mode == "home":
            return redirect(url_for("admin_home", **request.args.to_dict()))

        # Query params
        gender = request.args.get("gender", "").upper()  # "", "F", "M"
        pp = request.args.get("pp", "")
        include_pupil_ids = parse_id_csv(request.args.get("pupil_ids"))
        laps = request.args.get("laps", "")
        svc = request.args.get("svc", "")
        class_sel = request.args.get("class", None)  # "all" or class_id
        year_sel = request.args.get("year", None)    # year id or None
        term = (request.args.get("term") or "Autumn").strip()
        if term not in TERMS:
            term = "Autumn"

        classes = active_classes_query().order_by(SchoolClass.name).all()
        years = AcademicYear.query.order_by(AcademicYear.label.asc()).all()
        current_year = get_current_year()
        selected_year = None

        # Determine year
        if year_sel:
            try:
                yid = int(year_sel)
                selected_year = AcademicYear.query.get(yid)
            except (TypeError, ValueError):
                selected_year = None
        if not selected_year:
            selected_year = current_year

        # Determine scope: teacher's class, admin-all, or admin-one-class
        selected_is_current_year = bool(current_year and selected_year and current_year.id == selected_year.id)
        base_pupil_ids = None

        if is_admin:
            if class_sel is None:
                class_sel = "all"
            if class_sel == "all":
                klass = None
                selected_class_id = "all"
                if selected_is_current_year:
                    q = Pupil.query.join(SchoolClass).filter(
                        SchoolClass.is_archived.is_(False),
                        SchoolClass.is_archive.is_(False),
                    )
                else:
                    history_rows = (db.session.query(PupilClassHistory.pupil_id)
                                    .join(SchoolClass, SchoolClass.id == PupilClassHistory.class_id)
                                    .filter(
                                        PupilClassHistory.academic_year_id == selected_year.id,
                                        SchoolClass.is_archived.is_(False),
                                        SchoolClass.is_archive.is_(False),
                                    )
                                    .distinct()
                                    .all())
                    history_ids = [r[0] for r in history_rows]
                    if history_ids:
                        q = Pupil.query.filter(Pupil.id.in_(history_ids))
                    else:
                        q = Pupil.query.join(SchoolClass).filter(
                            SchoolClass.is_archived.is_(False),
                            SchoolClass.is_archive.is_(False),
                        )
            else:
                try:
                    class_id_int = int(class_sel)
                except (TypeError, ValueError):
                    class_id_int = None

                if class_id_int:
                    klass = SchoolClass.query.get(class_id_int)
                    if not klass or klass.is_archived or klass.is_archive:
                        klass = None
                        q = Pupil.query.join(SchoolClass).filter(
                            SchoolClass.is_archived.is_(False),
                            SchoolClass.is_archive.is_(False),
                        )
                        selected_class_id = "all"
                    else:
                        selected_class_id = str(klass.id)
                        base_pupil_ids = get_effective_pupil_ids_for(klass.id, selected_year.id)
                        if base_pupil_ids:
                            q = Pupil.query.filter(Pupil.id.in_(base_pupil_ids))
                        else:
                            q = Pupil.query.filter(Pupil.class_id == klass.id)
                else:
                    klass = None
                    q = Pupil.query.join(SchoolClass).filter(
                        SchoolClass.is_archived.is_(False),
                        SchoolClass.is_archive.is_(False),
                    )
                    selected_class_id = "all"
        else:
            allowed_ids = user_class_ids(current_user)
            if not allowed_ids:
                flash("No class assigned.", "error")
                return render_template(
                    "index.html",
                    classes=classes,
                    is_admin=False,
                    klass=None,
                    selected_class_id=None,
                    pupils=[],
                    results_by_pupil={},
                    kpi=None,
                    years=years,
                    selected_year_id=selected_year.id if selected_year else None,
                    filters={"gender": gender, "pp": pp, "laps": laps, "svc": svc},
                    gap_links=None,
                    subject=subject,
                    subject_label=subject_display(subject),
                    subjects=SUBJECTS,
                    paper_labels=paper_labels_for(subject),
                )

            requested_id = None
            if class_sel not in (None, "", "all"):
                try:
                    requested_id = int(class_sel)
                except (TypeError, ValueError):
                    requested_id = None

            chosen_id = requested_id if requested_id in allowed_ids else primary_class_id_for(current_user)
            if not chosen_id and allowed_ids:
                chosen_id = allowed_ids[0]

            klass = SchoolClass.query.get(chosen_id) if chosen_id else None
            if not klass or klass.is_archived or klass.is_archive:
                flash("No active class assigned.", "error")
                return redirect(url_for("logout"))
            selected_class_id = klass.id
            base_pupil_ids = get_effective_pupil_ids_for(klass.id, selected_year.id)
            if base_pupil_ids:
                q = Pupil.query.filter(Pupil.id.in_(base_pupil_ids))
            else:
                q = Pupil.query.filter(Pupil.class_id == klass.id)

        include_pupil_ids = parse_id_csv(request.args.get("pupil_ids"))
        if include_pupil_ids:
            q = q.filter(Pupil.id.in_(include_pupil_ids))

        # Apply filters
        if gender in ("F", "M"):
            q = q.filter(Pupil.gender == gender)

        def tri(qs, field, val):
            if val == "1":
                return qs.filter(field.is_(True))
            if val == "0":
                return qs.filter(field.is_(False))
            return qs

        q = tri(q, Pupil.pupil_premium, pp)
        q = tri(q, Pupil.laps, laps)
        q = tri(q, Pupil.service_child, svc)

        pupils = q.order_by(Pupil.number.is_(None), Pupil.number, Pupil.name).all()
        pupil_ids = [p.id for p in pupils]

        # Build latest result per term for this YEAR (+ annotate with css class)
        results_by_pupil = {p.id: {} for p in pupils}
        writing_by_pupil = {p.id: {} for p in pupils}

        if pupil_ids:
            if subject == "writing":
                writing_rows = (
                    WritingResult.query
                    .filter(WritingResult.pupil_id.in_(pupil_ids), WritingResult.academic_year_id == selected_year.id)
                    .order_by(WritingResult.created_at.desc())
                    .all()
                )
                for row in writing_rows:
                    if row.term in TERMS and row.term not in writing_by_pupil[row.pupil_id]:
                        row._band_css = writing_band_css(row.band)
                        row._band_label = writing_band_label(row.band)
                        writing_by_pupil[row.pupil_id][row.term] = row
            else:
                res = (
                    Result.query
                    .filter(Result.pupil_id.in_(pupil_ids), Result.academic_year_id == selected_year.id, Result.subject == subject)
                    .order_by(Result.created_at.desc())
                    .all()
                )
                for r in res:
                    if r.term in TERMS and r.term not in results_by_pupil[r.pupil_id]:
                        r._band_css = band_css(r.summary)
                        r._score_a, r._score_b = get_result_scores(r, subject)
                        results_by_pupil[r.pupil_id][r.term] = r

        # KPI calculators (year-filtered) incl. On-Track+
        def kpis_by_results(filtered_pupil_ids):
            stats = {}
            for t in TERMS:
                rq = (Result.query
                      .filter(Result.pupil_id.in_(filtered_pupil_ids),
                              Result.academic_year_id == selected_year.id,
                              Result.term == t,
                              Result.subject == subject))
                count = rq.count()
                if count == 0:
                    stats[t] = {
                        "count": 0, "wts": 0, "ot": 0, "gds": 0,
                        "pct_wts": 0.0, "pct_ot": 0.0, "pct_gds": 0.0,
                        "ot_plus": 0, "pct_ot_plus": 0.0,
                    }
                    continue
                cats = [(x.summary or "").lower() for x in rq.all()]
                wts = sum("towards" in c for c in cats)
                ot = sum("working at are" in c for c in cats)
                gds = sum("exceed" in c for c in cats)
                ot_plus = ot + gds
                stats[t] = {
                    "count": count,
                    "wts": wts, "ot": ot, "gds": gds,
                    "pct_wts": round(wts / count * 100.0, 1),
                    "pct_ot": round(ot / count * 100.0, 1),
                    "pct_gds": round(gds / count * 100.0, 1),
                    "ot_plus": ot_plus,
                    "pct_ot_plus": round(ot_plus / count * 100.0, 1),
                }
            return stats

        def kpis_by_pupil(filtered_pupils, latest_by_pupil_term):
            total_pupils = len(filtered_pupils)
            stats = {}
            for t in TERMS:
                wts = ot = gds = 0
                for p in filtered_pupils:
                    r = latest_by_pupil_term.get(p.id, {}).get(t)
                    if not r or not r.summary:
                        continue
                    s = (r.summary or "").lower()
                    if "towards" in s:
                        wts += 1
                    elif "working at are" in s:
                        ot += 1
                    elif "exceed" in s:
                        gds += 1

                if total_pupils == 0:
                    stats[t] = {"count": 0, "wts": 0, "ot": 0, "gds": 0,
                                "pct_wts": 0.0, "pct_ot": 0.0, "pct_gds": 0.0,
                                "ot_plus": 0, "pct_ot_plus": 0.0}
                else:
                    count_with_result = sum(
                        1 for p in filtered_pupils if latest_by_pupil_term.get(p.id, {}).get(t)
                    )
                    ot_plus = ot + gds
                    stats[t] = {
                        "count": count_with_result,
                        "wts": wts, "ot": ot, "gds": gds,
                        "pct_wts": round(wts / total_pupils * 100.0, 1),
                        "pct_ot": round(ot / total_pupils * 100.0, 1),
                        "pct_gds": round(gds / total_pupils * 100.0, 1),
                        "ot_plus": ot_plus,
                        "pct_ot_plus": round(ot_plus / total_pupils * 100.0, 1),
                    }
            return stats

        if subject == "writing":
            def writing_kpis():
                stats = {}
                for t in TERMS:
                    term_rows = [writing_by_pupil.get(pid, {}).get(t) for pid in pupil_ids]
                    term_rows = [r for r in term_rows if r]
                    count = len(term_rows)
                    if count == 0:
                        stats[t] = {"count": 0, "wts": 0, "ot": 0, "gds": 0,
                                    "pct_wts": 0.0, "pct_ot": 0.0, "pct_gds": 0.0,
                                    "ot_plus": 0, "pct_ot_plus": 0.0}
                        continue
                    wts = sum(r.band == "working_towards" for r in term_rows)
                    ot = sum(r.band == "working_at" for r in term_rows)
                    gds = sum(r.band == "exceeding" for r in term_rows)
                    ot_plus = ot + gds
                    stats[t] = {
                        "count": count,
                        "wts": wts, "ot": ot, "gds": gds,
                        "pct_wts": round(wts / count * 100.0, 1),
                        "pct_ot": round(ot / count * 100.0, 1),
                        "pct_gds": round(gds / count * 100.0, 1),
                        "ot_plus": ot_plus,
                        "pct_ot_plus": round(ot_plus / count * 100.0, 1),
                    }
                return stats
            kpi = writing_kpis()
        else:
            kpi = kpis_by_results(pupil_ids) if request.args.get("pp") != "1" else kpis_by_pupil(pupils, results_by_pupil)

        action_needed = build_action_needed(current_user, selected_year.id, term)

        # Build GAP analysis links
        gap_links = None
        if subject != "writing" and selected_year and (not is_admin or (is_admin and klass is not None)):
            gap_links = {}
            for t in TERMS:
                gap_links[t] = {}
                for paper in PAPERS[subject]:
                    a = get_or_create_assessment_for(klass.id, selected_year.id, t, subject, paper)
                    gap_links[t][paper] = a.id
            db.session.commit()

        filters = {
            "gender": request.args.get("gender", ""),
            "pp": request.args.get("pp", ""),
            "laps": request.args.get("laps", ""),
            "svc": request.args.get("svc", ""),
            "min_pct": request.args.get("min_pct", ""),
            "max_pct": request.args.get("max_pct", ""),
            "band": request.args.get("band", ""),
            "term": term,
        }

        overview_chart = None
        if not is_admin and mode == "home" and klass and selected_year:
            base_q = Pupil.query.filter(Pupil.class_id == klass.id)
            base_q = apply_group_filters(base_q, gender=gender, pp=pp, laps=laps, svc=svc)
            overview_pupil_ids = [p.id for p in base_q.all()]
            overview_chart = subject_distribution_for_pupil_ids(overview_pupil_ids, selected_year.id, term, subject)
            overview_chart["subject"] = subject
            overview_chart["term"] = term

        if klass is not None and klass.year_group == 6 and mode == "home":
            return redirect(url_for("y6_home", **request.args.to_dict()))

        return render_template(
            "index.html",
            is_admin=is_admin,
            classes=classes,
            years=years,
            selected_year_id=selected_year.id if selected_year else None,
            klass=klass,
            selected_class_id=selected_class_id,
            pupils=pupils,
            results_by_pupil=results_by_pupil,
            kpi=kpi,
            filters=filters,
            gap_links=gap_links,
            subject=subject,
            subject_label=("Writing" if subject == "writing" else subject_display(subject)),
            subjects=SUBJECTS + ("writing",),
            paper_labels=(("Band", "") if subject == "writing" else paper_labels_for(subject)),
            gap_papers=(PAPERS[subject] if subject != "writing" else []),
            writing_by_pupil=writing_by_pupil,
            writing_bands=WRITING_BANDS,
            writing_band_label=writing_band_label,
            action_needed=action_needed,
            mode=mode,
            overview_chart=overview_chart,
        )



    @app.route("/writing")
    @login_required
    def writing_dashboard():
        class_sel = request.args.get("class")
        year_sel = request.args.get("year")
        params = {"subject": "writing"}
        if class_sel not in (None, ""):
            params["class"] = class_sel
        if year_sel not in (None, ""):
            params["year"] = year_sel
        return redirect(url_for("dashboard", **params))

    @app.route("/api/dashboard/summary")
    @login_required
    def api_dashboard_summary():
        year = parse_year_id_or_current(request.args.get("year"))
        if not year:
            return jsonify({"error": "No year selected"}), 400
        term = (request.args.get("term") or "Autumn").strip()
        if term not in TERMS:
            term = "Autumn"
        subject = (request.args.get("subject") or "maths").strip().lower()
        if subject not in (*SUBJECTS, "writing", "all"):
            subject = "maths"
        group = (request.args.get("group") or "all").strip().lower()
        if group not in ("all", "boys", "girls", "pp", "non_pp", "laps", "service"):
            group = "all"
        class_raw = request.args.get("class")
        class_id = None
        if class_raw not in (None, "", "all", "0"):
            try:
                class_id = int(class_raw)
            except ValueError:
                class_id = None
        if class_id:
            require_class_access(class_id)

        payload = dashboard_summary_payload(year.id, term, subject, group, class_id)
        payload["filters"] = {
            "year": year.id,
            "term": term,
            "subject": subject,
            "group": group,
            "class": class_id,
        }
        return jsonify(payload)

    @app.route("/year6/home")
    @login_required
    def y6_home():
        is_admin = bool(getattr(current_user, "is_admin", False))
        is_teacher = bool(getattr(current_user, "is_teacher", not is_admin))
        if not (is_teacher or is_admin):
            abort(403)

        ensure_default_year()
        selected_year = parse_year_id_or_current(request.args.get("year"))
        class_sel = request.args.get("class")
        subject_sel = (request.args.get("subject") or "all").strip().lower()
        if subject_sel not in ("all", "maths", "reading", "spag"):
            subject_sel = "all"

        if is_admin:
            klass = None
            if class_sel not in (None, "", "all"):
                try:
                    klass = SchoolClass.query.get(int(class_sel))
                except (TypeError, ValueError):
                    klass = None
            if klass is None:
                klass = (active_classes_query()
                         .filter(SchoolClass.year_group == 6)
                         .order_by(SchoolClass.name.asc())
                         .first())
        else:
            class_id = primary_class_id_for(current_user)
            klass = SchoolClass.query.get(class_id) if class_id else None

        if not klass or klass.year_group != 6 or klass.is_archived or klass.is_archive:
            flash("Year 6 Home is only available for active Year 6 classes.", "error")
            return redirect(url_for("dashboard", subject="maths", mode="home"))

        ensure_sats_headers(klass.id, selected_year.id)

        filter_values = {
            "gender": (request.args.get("gender") or "all").strip().lower(),
            "pp": (request.args.get("pp") or "all").strip().lower(),
            "laps": (request.args.get("laps") or "all").strip().lower(),
            "service": (request.args.get("service") or "all").strip().lower(),
        }
        valid_filters = {
            "gender": {"all", "male", "female"},
            "pp": {"all", "pp", "nonpp"},
            "laps": {"all", "laps", "nonlaps"},
            "service": {"all", "service", "nonservice"},
        }
        for key, allowed in valid_filters.items():
            if filter_values[key] not in allowed:
                filter_values[key] = "all"

        rows = build_year6_home_rows(klass.id, selected_year.id, filter_values)
        overview = year6_sats_overview(rows)

        filters = {
            "gender": filter_values["gender"],
            "pp": filter_values["pp"],
            "laps": filter_values["laps"],
            "service": filter_values["service"],
            "subject": subject_sel,
        }

        return render_template(
            "home_year6.html",
            klass=klass,
            selected_year=selected_year,
            selected_year_id=selected_year.id,
            selected_class_id=str(klass.id),
            is_admin=is_admin,
            classes=active_classes_query().order_by(SchoolClass.name.asc()).all(),
            years=AcademicYear.query.order_by(AcademicYear.label.asc()).all(),
            filters=filters,
            rows=rows,
            overview=overview,
            score_class=sats_score_class,
            is_editable=(not is_admin and user_has_class_access(current_user, klass.id)),
        )

    @app.route("/year6/sats/update", methods=["POST"])
    @login_required
    def year6_sats_update():
        try:
            class_id = int(request.form.get("class_id", "0"))
            year_id = int(request.form.get("year_id", "0"))
        except (TypeError, ValueError):
            abort(400)

        klass = SchoolClass.query.get_or_404(class_id)
        if klass.year_group != 6:
            abort(400)

        can_edit = bool(getattr(current_user, "is_admin", False)) or user_has_class_access(current_user, class_id)
        if not can_edit:
            abort(403)

        ensure_sats_headers(class_id, year_id)

        for form_key, raw_value in request.form.items():
            if not form_key.startswith("score__"):
                continue

            parts = form_key.split("__", 2)
            if len(parts) != 3:
                continue

            try:
                pupil_id = int(parts[1])
            except (TypeError, ValueError):
                continue

            db_key = parse_year6_cell_key(parts[2])
            if not db_key:
                continue

            pupil = Pupil.query.get(pupil_id)
            if not pupil or pupil.class_id != class_id:
                continue

            value_text = (raw_value or "").strip()
            if value_text == "":
                parsed = None
            else:
                try:
                    parsed = float(value_text)
                except ValueError:
                    continue

            existing = SatsScore.query.filter_by(
                pupil_id=pupil_id,
                academic_year_id=year_id,
                key=db_key,
            ).first()
            if not existing:
                existing = SatsScore(
                    pupil_id=pupil_id,
                    academic_year_id=year_id,
                    key=db_key,
                )
                db.session.add(existing)

            existing.value = parsed
            existing.updated_at = datetime.utcnow()

        db.session.commit()

        return_endpoint = (request.form.get("return_endpoint") or "y6_home").strip()
        if return_endpoint not in {"y6_home", "sats_page"}:
            return_endpoint = "y6_home"

        redirect_params = {
            key.replace("return__", "", 1): value
            for key, value in request.form.items()
            if key.startswith("return__")
        }

        if "class" not in redirect_params:
            redirect_params["class"] = str(class_id)
        if "year" not in redirect_params:
            redirect_params["year"] = str(year_id)

        return redirect(url_for(return_endpoint, **redirect_params))

    @app.route("/year6/sats-tracker")
    @login_required
    def y6_sats_tracker():
        params = {}
        class_sel = request.args.get("class")
        year_sel = request.args.get("year")

        if getattr(current_user, "is_admin", False):
            selected_class = None
            if class_sel not in (None, "", "all"):
                try:
                    selected_class = SchoolClass.query.get(int(class_sel))
                except (TypeError, ValueError):
                    selected_class = None

            if not selected_class or selected_class.year_group != 6 or selected_class.is_archived or selected_class.is_archive:
                selected_class = (active_classes_query()
                                  .filter(SchoolClass.year_group == 6)
                                  .order_by(SchoolClass.name.asc())
                                  .first())

            if not selected_class:
                flash("No active Year 6 class is available for SATs tracker.", "error")
                return redirect(url_for("dashboard", subject="maths", mode="home"))

            params["class"] = selected_class.id
        elif class_sel not in (None, ""):
            params["class"] = class_sel

        if year_sel not in (None, ""):
            params["year"] = year_sel
        return redirect(url_for("sats_page", **params))

    @app.route("/year6/writing")
    @login_required
    def y6_writing():
        params = {"subject": "writing", "mode": "table"}
        class_sel = request.args.get("class")
        year_sel = request.args.get("year")
        klass = None
        if class_sel not in (None, "", "all"):
            try:
                klass = SchoolClass.query.get(int(class_sel))
            except (TypeError, ValueError):
                klass = None

        if klass is None:
            if getattr(current_user, "is_admin", False):
                klass = (active_classes_query()
                         .filter(SchoolClass.year_group == 6)
                         .order_by(SchoolClass.name.asc())
                         .first())
            else:
                class_id = primary_class_id_for(current_user)
                klass = SchoolClass.query.get(class_id) if class_id else None

        if not klass or klass.year_group != 6 or klass.is_archived or klass.is_archive:
            flash("Year 6 Writing is only available for active Year 6 classes.", "error")
            return redirect(url_for("dashboard", subject="writing", mode="table"))

        params["class"] = klass.id
        if year_sel not in (None, ""):
            params["year"] = year_sel
        return redirect(url_for("dashboard", **params))

    @app.route("/year6/reports")
    @login_required
    def y6_reports():
        params = {}
        class_sel = request.args.get("class")
        year_sel = request.args.get("year")
        if class_sel not in (None, ""):
            params["class"] = class_sel
        if year_sel not in (None, ""):
            params["year"] = year_sel
        return redirect(url_for("reports", **params))

    @app.route("/api/writing/quick_save", methods=["POST"])

    @login_required
    def api_writing_quick_save():
        data = request.get_json(silent=True) or {}

        pupil_id = int(data.get("pupil_id", 0))
        year_id = int(data.get("year_id", 0))
        term = data.get("term")
        band = (data.get("band") or "").strip().lower().replace(" ", "_")
        note = data.get("note")

        if term not in TERMS:
            abort(400)
        if band and band not in WRITING_BANDS:
            abort(400)

        pupil = Pupil.query.get_or_404(pupil_id)
        if not getattr(current_user, "is_admin", False) and not user_has_class_access(current_user, pupil.class_id):
            abort(403)

        existing = WritingResult.query.filter_by(
            pupil_id=pupil.id,
            academic_year_id=year_id,
            term=term,
        ).first()

        if not band:
            if existing:
                db.session.delete(existing)
                db.session.commit()
            return jsonify({"ok": True, "band": None, "band_label": None, "band_css": None})

        if existing:
            existing.band = band
            if note is not None:
                existing.note = str(note).strip() or None
        else:
            existing = WritingResult(
                pupil_id=pupil.id,
                academic_year_id=year_id,
                term=term,
                band=band,
                note=(str(note).strip() if note is not None else None) or None,
            )
            db.session.add(existing)

        db.session.commit()
        return jsonify({
            "ok": True,
            "band": existing.band,
            "band_label": writing_band_label(existing.band),
            "band_css": writing_band_css(existing.band),
        })

    # ---- Auth

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard", subject="maths"))
        form = LoginForm()
        if form.validate_on_submit():
            user = Teacher.query.filter_by(username=form.username.data.strip()).first()
            if user and user.check_password(form.password.data):
                if not getattr(user, "is_active", True):
                    flash("This account is disabled.", "error")
                    return render_template("login.html", form=form)
                login_user(user)
                return redirect(url_for("dashboard", subject="maths"))
            flash("Invalid username or password", "error")
        return render_template("login.html", form=form)

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("index"))

    # ---- Pupil CRUD (create only here)

    @app.route("/pupils/new", methods=["GET", "POST"])
    @login_required
    def pupil_new():
        if getattr(current_user, "is_admin", False):
            flash("Admin cannot add pupils from here.", "error")
            return redirect(url_for("dashboard", subject="maths"))
        if not primary_class_id_for(current_user):
            flash("No class assigned.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        form = PupilForm()
        if form.validate_on_submit():
            # Max 32 pupils per class
            count = Pupil.query.filter_by(class_id=primary_class_id_for(current_user)).count()
            if count >= 32:
                flash("This class already has 32 pupils.", "error")
                return redirect(url_for("dashboard", subject="maths"))
            p = Pupil(
                class_id=primary_class_id_for(current_user),
                number=form.number.data,
                name=form.name.data.strip(),
                gender=form.gender.data or None,
                pupil_premium=form.pupil_premium.data,
                laps=form.laps.data,
                service_child=form.service_child.data,
            )
            db.session.add(p)
            db.session.commit()
            flash("Pupil added.", "success")
            return redirect(url_for("dashboard", subject="maths"))
        return render_template("pupil_form.html", form=form, title="Add pupil")

    # ---- Result add

    @app.route("/results/new/<int:pupil_id>", methods=["GET", "POST"])
    @login_required
    def result_new(pupil_id):
        pupil = Pupil.query.get_or_404(pupil_id)
        subject = normalize_subject(request.args.get("subject", "maths"))

        # Permissions: teacher may only edit pupils in their class
        require_pupil_access(pupil)

        form = ResultForm()
        form.academic_year.choices = academic_year_choices()

        if form.validate_on_submit():
            ar = form.arithmetic.data
            rs = form.reasoning.data
            year_id = form.academic_year.data
            term = form.term.data
            note = form.note.data

            existing = Result.query.filter_by(
                pupil_id=pupil.id,
                academic_year_id=year_id,
                term=term,
                subject=subject
            ).first()

            combined_pct, summary = compute_combined_and_band(ar, rs, pupil.class_id, term, year_id, subject)

            if existing:
                set_result_scores(existing, subject, ar, rs)
                existing.updated_by_teacher_id = current_user.id
                existing.note = note
                existing.combined_pct = combined_pct
                existing.summary = summary
                existing.class_id_snapshot = pupil.class_id
            else:
                new_result = Result(
                    pupil_id=pupil.id,
                    academic_year_id=year_id,
                    class_id_snapshot=pupil.class_id,
                    term=term,
                    note=note,
                    combined_pct=combined_pct,
                    summary=summary,
                    subject=subject
                )
                set_result_scores(new_result, subject, ar, rs)
                new_result.updated_by_teacher_id = current_user.id
                db.session.add(new_result)

            db.session.commit()
            flash("Result saved.", "success")

            # Save & Next pupil
            if request.form.get("save_next") == "1":
                pupils = (
                    Pupil.query
                    .filter_by(class_id=pupil.class_id)
                    .order_by(Pupil.number.is_(None), Pupil.number, Pupil.name)
                    .all()
                )
                ids = [p.id for p in pupils]
                if pupil.id in ids and len(ids) > 1:
                    nxt = pupils[(ids.index(pupil.id) + 1) % len(pupils)]
                    return redirect(url_for("result_new", pupil_id=nxt.id, subject=subject))

            return redirect(url_for("dashboard", subject="maths", year=year_id, **{"class": pupil.class_id}))

        return render_template(
            "result_form.html",
            form=form,
            pupil=pupil,
            title=f"Add {subject_display(subject)} result for {pupil.name}",
            subject=subject,
            paper_labels=paper_labels_for(subject)
        )

    # ---- API: inline score autosave (NO REDIRECT)

    @app.route("/api/results/quick_save", methods=["POST"])
    @login_required
    def api_results_quick_save():
        data = request.get_json(silent=True) or {}

        pupil_id = int(data.get("pupil_id", 0))
        year_id  = int(data.get("year_id", 0))
        term     = data.get("term")
        field    = data.get("field")   # "arithmetic" or "reasoning"
        value    = data.get("value")   # string/number/None
        subject  = (data.get("subject") or "maths").strip().lower()

        if term not in ("Autumn", "Spring", "Summer"):
            abort(400)
        if field not in ("arithmetic", "reasoning"):
            abort(400)
        if subject not in SUBJECTS:
            abort(400)

        pupil = Pupil.query.get_or_404(pupil_id)

        require_pupil_access(pupil)

        # allow blank to clear
        if value is None or str(value).strip() == "":
            v = None
        else:
            try:
                v = float(str(value).strip())
            except ValueError:
                return jsonify({"ok": False, "error": "Score must be a number"}), 400

        existing = Result.query.filter_by(
            pupil_id=pupil.id,
            academic_year_id=year_id,
            term=term,
            subject=subject
        ).first()

        if not existing:
            existing = Result(
                pupil_id=pupil.id,
                academic_year_id=year_id,
                class_id_snapshot=pupil.class_id,
                term=term,
                subject=subject
            )
            db.session.add(existing)
            db.session.flush()

        paper = PAPERS[subject][0] if field == "arithmetic" else PAPERS[subject][1]
        setattr(existing, result_field_for_paper(subject, paper), v)
        existing.updated_by_teacher_id = current_user.id

        try:
            combined_pct, summary = compute_combined_and_band(
                *get_result_scores(existing, subject), pupil.class_id, term, year_id, subject
            )
        except Exception:
            combined_pct, summary = None, None

        existing.combined_pct = combined_pct
        existing.summary = summary
        existing.class_id_snapshot = pupil.class_id

        db.session.commit()
        return jsonify({"ok": True, "combined_pct": combined_pct, "summary": summary})

    # ---- API: inline pupil updates (PP/LAPS/SVC etc)

    @app.route("/api/pupils/quick_update", methods=["POST"])
    @login_required
    def api_pupils_quick_update():
        data = request.get_json(silent=True) or {}
        pupil_id = int(data.get("pupil_id", 0))
        field = data.get("field")
        value = data.get("value")

        if field not in ("pupil_premium", "laps", "service_child", "gender", "number", "name"):
            abort(400)

        pupil = Pupil.query.get_or_404(pupil_id)

        require_pupil_access(pupil)

        if field in ("pupil_premium", "laps", "service_child"):
            setattr(pupil, field, bool(value))
            pupil.updated_at = datetime.utcnow()
        elif field == "gender":
            g = (str(value or "")).strip().upper()
            pupil.gender = g if g in ("M", "F") else None
            pupil.updated_at = datetime.utcnow()
        elif field == "number":
            s = str(value or "").strip()
            pupil.number = int(s) if s.isdigit() else None
            pupil.updated_at = datetime.utcnow()
        elif field == "name":
            name = str(value or "").strip()
            if not name:
                return jsonify({"ok": False, "error": "Name cannot be blank"}), 400
            pupil.name = name
            pupil.updated_at = datetime.utcnow()

        db.session.commit()
        return jsonify({"ok": True})

    # ---- API: inline quick add pupil (teachers)

    @app.route("/api/pupils/quick_add", methods=["POST"])
    @login_required
    def api_pupils_quick_add():
        if getattr(current_user, "is_admin", False):
            abort(403)
        if not primary_class_id_for(current_user):
            abort(400)

        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()
        gender = str(data.get("gender") or "").strip().upper()
        number = data.get("number")
        pp = bool(data.get("pupil_premium"))
        laps = bool(data.get("laps"))
        svc = bool(data.get("service_child"))

        if not name:
            return jsonify({"ok": False, "error": "Name is required"}), 400

        count = Pupil.query.filter_by(class_id=primary_class_id_for(current_user)).count()
        if count >= 32:
            return jsonify({"ok": False, "error": "This class already has 32 pupils"}), 400

        n = None
        try:
            if number not in (None, "", " "):
                n = int(str(number).strip())
        except ValueError:
            n = None

        g = gender if gender in ("M", "F") else None

        p = Pupil(
            class_id=primary_class_id_for(current_user),
            number=n,
            name=name,
            gender=g,
            pupil_premium=pp,
            laps=laps,
            service_child=svc,
        )
        db.session.add(p)
        db.session.commit()

        return jsonify({"ok": True, "pupil_id": p.id})



    @app.route("/pupil/<int:pupil_id>")
    @login_required
    def pupil_page(pupil_id: int):
        ensure_default_year()
        pupil = Pupil.query.get_or_404(pupil_id)
        requested_subject = (request.args.get("subject", "maths") or "maths").strip().lower()
        if requested_subject == "writing":
            subject = "writing"
        else:
            subject = normalize_subject(requested_subject)

        require_pupil_access(pupil)

        year_param = request.args.get("year")
        if year_param:
            try:
                year = AcademicYear.query.get(int(year_param))
            except ValueError:
                year = get_current_year()
            if not year:
                year = get_current_year()
        else:
            year = get_current_year()

        records = []
        if subject == "writing":
            order = case(
                (WritingResult.term == "Autumn", 1),
                (WritingResult.term == "Spring", 2),
                (WritingResult.term == "Summer", 3),
                else_=4,
            )
            result_map = {t: None for t in TERMS}
            results = (
                WritingResult.query
                .filter_by(pupil_id=pupil.id, academic_year_id=year.id)
                .order_by(order)
                .all()
            )
            for r in results:
                if r.term in result_map and result_map[r.term] is None:
                    result_map[r.term] = r

            for t in TERMS:
                r = result_map[t]
                band = writing_band_label(r.band) if r else None
                records.append({
                    "term": t,
                    "arithmetic": "—",
                    "reasoning": "—",
                    "combined_pct": "—",
                    "summary": band,
                    "note": (r.note if r and r.note else ""),
                    "delta": None,
                })
        else:
            order = case(
                (Result.term == "Autumn", 1),
                (Result.term == "Spring", 2),
                (Result.term == "Summer", 3),
                else_=4,
            )

            result_map = {t: None for t in TERMS}
            results = (
                Result.query
                .filter_by(pupil_id=pupil.id, academic_year_id=year.id, subject=subject)
                .order_by(order)
                .all()
            )
            for r in results:
                if r.term in result_map and result_map[r.term] is None:
                    result_map[r.term] = r

            prev_combined = None
            for t in TERMS:
                r = result_map[t]
                combined = r.combined_pct if r else None
                delta = None if combined is None or prev_combined is None else round(combined - prev_combined, 1)
                records.append({
                    "term": t,
                    "arithmetic": (r.arithmetic if r else None),
                    "reasoning": (r.reasoning if r else None),
                    "combined_pct": combined,
                    "summary": (r.summary if r else None),
                    "note": (r.note if r and r.note else ""),
                    "delta": delta,
                })
                if combined is not None:
                    prev_combined = combined

        years = AcademicYear.query.order_by(AcademicYear.label.asc()).all()

        return render_template(
            "pupil.html",
            pupil=pupil,
            records=records,
            years=years,
            selected_year_id=year.id,
            subject=subject,
            subject_label=("Writing" if subject == "writing" else subject_display(subject)),
            subjects=SUBJECTS,
            paper_labels=(("Band", "") if subject == "writing" else paper_labels_for(subject)),
            band_css=band_css,
            is_year6=(pupil.klass.year_group == 6),
        )



    @app.route("/sats")
    @login_required
    def sats_page():
        ensure_default_year()
        year = parse_year_id_or_current(request.args.get("year"))
        class_sel = request.args.get("class")

        if getattr(current_user, "is_admin", False):
            if class_sel in (None, "", "all"):
                flash("Choose a class to view SATs tracker.", "error")
                return redirect(url_for("dashboard", subject="maths", year=(year.id if year else None)))
            try:
                class_id = int(class_sel)
            except (TypeError, ValueError):
                flash("Invalid class selection.", "error")
                return redirect(url_for("dashboard", subject="maths"))
            klass = SchoolClass.query.get_or_404(class_id)
        else:
            if not primary_class_id_for(current_user):
                flash("No class assigned.", "error")
                return redirect(url_for("dashboard", subject="maths"))
            klass = SchoolClass.query.get_or_404(primary_class_id_for(current_user))

        if klass.is_archived or klass.is_archive:
            flash("SATs tracker is not available for archived classes.", "error")
            return redirect(url_for("dashboard", subject="maths", year=year.id, **{"class": klass.id}))

        if klass.year_group != 6:
            flash("SATs tracker is only available for Year 6 classes.", "error")
            return redirect(url_for("dashboard", subject="maths", year=year.id, **{"class": klass.id}))

        ensure_sats_headers(klass.id, year.id)

        rows = build_year6_home_rows(
            klass.id,
            year.id,
            {"gender": "all", "pp": "all", "laps": "all", "service": "all"},
        )

        return render_template(
            "index_y6_sats.html",
            klass=klass,
            rows=rows,
            years=AcademicYear.query.order_by(AcademicYear.label.asc()).all(),
            selected_year_id=year.id,
            selected_class_id=str(klass.id),
            is_admin=bool(getattr(current_user, "is_admin", False)),
            classes=active_classes_query().order_by(SchoolClass.name).all(),
            filters={"subject": "all"},
            score_class=sats_score_class,
            is_editable=(not bool(getattr(current_user, "is_admin", False))),
        )

    @app.route("/api/sats/rename_header", methods=["POST"])
    @login_required
    def api_sats_rename_header():
        data = request.get_json(silent=True) or {}
        try:
            class_id = int(data.get("class_id", 0))
            year_id = int(data.get("year_id", 0))
        except (TypeError, ValueError):
            abort(400)

        key = (data.get("key") or "").strip()
        header = (data.get("header") or "").strip()

        if not key:
            abort(400)

        klass = SchoolClass.query.get_or_404(class_id)
        if not user_can_access_class(klass):
            abort(403)

        ensure_sats_headers(class_id, year_id)
        row = SatsHeader.query.filter_by(
            class_id=class_id, academic_year_id=year_id, key=key
        ).first_or_404()
        row.header = header
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/api/sats/quick_save", methods=["POST"])
    @login_required
    def api_sats_quick_save():
        data = request.get_json(silent=True) or {}
        try:
            pupil_id = int(data.get("pupil_id", 0))
            year_id = int(data.get("year_id", 0))
        except (TypeError, ValueError):
            abort(400)

        key = (data.get("key") or "").strip()
        value = data.get("value")

        if not key:
            abort(400)

        pupil = Pupil.query.get_or_404(pupil_id)
        klass = SchoolClass.query.get_or_404(pupil.class_id)
        if not user_can_access_class(klass):
            abort(403)

        ensure_sats_headers(klass.id, year_id)

        col = SatsHeader.query.filter_by(
            class_id=klass.id, academic_year_id=year_id, key=key
        ).first()
        if not col:
            abort(400)

        if value is None or str(value).strip() == "":
            parsed = None
        else:
            try:
                parsed = float(str(value).strip())
            except ValueError:
                return jsonify({"ok": False, "error": "Score must be a number"}), 400

        existing = SatsScore.query.filter_by(
            pupil_id=pupil.id,
            academic_year_id=year_id,
            key=key
        ).first()

        if not existing:
            existing = SatsScore(
                pupil_id=pupil.id,
                academic_year_id=year_id,
                key=key,
            )
            db.session.add(existing)

        existing.value = parsed
        existing.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/pupil/<int:pupil_id>/sats")
    @login_required
    def pupil_sats_page(pupil_id: int):
        ensure_default_year()
        pupil = Pupil.query.get_or_404(pupil_id)
        if not getattr(current_user, "is_admin", False) and not user_has_class_access(current_user, pupil.class_id):
            flash("You don't have access to this pupil.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        klass = SchoolClass.query.get_or_404(pupil.class_id)
        if klass.year_group != 6:
            flash("SATs report is only available for Year 6 pupils.", "error")
            return redirect(url_for("pupil_page", pupil_id=pupil.id))

        year = parse_year_id_or_current(request.args.get("year"))
        ensure_sats_headers(klass.id, year.id)

        headers = (SatsHeader.query
                   .filter_by(class_id=klass.id, academic_year_id=year.id)
                   .order_by(SatsHeader.order.asc(), SatsHeader.id.asc())
                   .all())
        headers_by_group = {"Maths": [], "Reading": [], "SPaG": []}
        for h in headers:
            headers_by_group.setdefault(h.group, []).append(h)

        score_rows = SatsScore.query.filter_by(
            pupil_id=pupil.id, academic_year_id=year.id
        ).all()
        score_map = {s.key: s.value for s in score_rows}

        return render_template(
            "pupil_sats.html",
            pupil=pupil,
            klass=klass,
            years=AcademicYear.query.order_by(AcademicYear.label.asc()).all(),
            selected_year_id=year.id,
            headers_by_group=headers_by_group,
            score_map=score_map,
        )

    # ---- Term settings (per class & year)

    @app.route("/settings/terms", methods=["GET", "POST"])
    @login_required
    def term_settings():
        # Only teachers manage their class settings here
        if getattr(current_user, "is_admin", False):
            flash("Admin cannot edit term settings here.", "error")
            return redirect(url_for("dashboard", subject="maths"))
        if not primary_class_id_for(current_user):
            flash("No class assigned.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        klass = SchoolClass.query.get_or_404(primary_class_id_for(current_user))
        ensure_default_year()
        form = TermSettingsForm()
        form.academic_year.choices = academic_year_choices()

        # Ensure cfg rows for all three terms in selected year
        def get_or_make(year_id, term):
            row = TermConfig.query.filter_by(
                class_id=klass.id,
                academic_year_id=year_id,
                term=term,
            ).first()
            if not row:
                row = TermConfig(
                    class_id=klass.id,
                    academic_year_id=year_id,
                    term=term,
                    arith_max=50,
                    reason_max=50,
                    reading_p1_max=50,
                    reading_p2_max=50,
                    spelling_max=20,
                    grammar_max=40,
                )
                db.session.add(row)
                db.session.flush()
            return row

        # GET: populate from selected year (or current year)
        if request.method == "GET":
            year = get_current_year()
            form.academic_year.data = year.id
            a = get_or_make(year.id, "Autumn")
            s = get_or_make(year.id, "Spring")
            u = get_or_make(year.id, "Summer")
            db.session.commit()

            form.autumn_arith_max.data = a.arith_max
            form.autumn_reason_max.data = a.reason_max
            form.autumn_reading_p1_max.data = a.reading_p1_max
            form.autumn_reading_p2_max.data = a.reading_p2_max
            form.autumn_spelling_max.data = a.spelling_max
            form.autumn_grammar_max.data = a.grammar_max
            form.spring_arith_max.data = s.arith_max
            form.spring_reason_max.data = s.reason_max
            form.spring_reading_p1_max.data = s.reading_p1_max
            form.spring_reading_p2_max.data = s.reading_p2_max
            form.spring_spelling_max.data = s.spelling_max
            form.spring_grammar_max.data = s.grammar_max
            form.summer_arith_max.data = u.arith_max
            form.summer_reason_max.data = u.reason_max
            form.summer_reading_p1_max.data = u.reading_p1_max
            form.summer_reading_p2_max.data = u.reading_p2_max
            form.summer_spelling_max.data = u.spelling_max
            form.summer_grammar_max.data = u.grammar_max

        if form.validate_on_submit():
            year_id = form.academic_year.data
            a = get_or_make(year_id, "Autumn")
            s = get_or_make(year_id, "Spring")
            u = get_or_make(year_id, "Summer")

            a.arith_max, a.reason_max = (form.autumn_arith_max.data, form.autumn_reason_max.data)
            a.reading_p1_max, a.reading_p2_max = (form.autumn_reading_p1_max.data, form.autumn_reading_p2_max.data)
            a.spelling_max, a.grammar_max = (form.autumn_spelling_max.data, form.autumn_grammar_max.data)
            s.arith_max, s.reason_max = (form.spring_arith_max.data, form.spring_reason_max.data)
            s.reading_p1_max, s.reading_p2_max = (form.spring_reading_p1_max.data, form.spring_reading_p2_max.data)
            s.spelling_max, s.grammar_max = (form.spring_spelling_max.data, form.spring_grammar_max.data)
            u.arith_max, u.reason_max = (form.summer_arith_max.data, form.summer_reason_max.data)
            u.reading_p1_max, u.reading_p2_max = (form.summer_reading_p1_max.data, form.summer_reading_p2_max.data)
            u.spelling_max, u.grammar_max = (form.summer_spelling_max.data, form.summer_grammar_max.data)
            db.session.commit()

            # Keep GAP assessments in sync with maxima
            sync_gap_assessments_for_class_year(klass.id, year_id)

            flash("Term settings saved and GAP analyses synced.", "success")
            return redirect(url_for("dashboard", subject="maths"))

        return render_template("settings_terms.html", form=form, klass=klass)

    # ---- Class settings (year group)

    @app.route("/settings/class", methods=["GET", "POST"])
    @login_required
    def class_settings():
        # Teachers set their own class year group
        if getattr(current_user, "is_admin", False):
            flash("Admin cannot edit class settings here.", "error")
            return redirect(url_for("dashboard", subject="maths"))
        if not primary_class_id_for(current_user):
            flash("No class assigned.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        klass = SchoolClass.query.get_or_404(primary_class_id_for(current_user))
        form = ClassSettingsForm()

        if request.method == "GET":
            form.year_group.data = klass.year_group or 6  # default

        if form.validate_on_submit():
            klass.year_group = form.year_group.data
            db.session.commit()
            flash("Class settings saved.", "success")
            return redirect(url_for("dashboard", subject="maths"))

        return render_template("settings_class.html", form=form, klass=klass)

    # ---- Admin: Academic years

    @app.route("/admin/years", methods=["GET", "POST"])
    @login_required
    def admin_years():
        if not getattr(current_user, "is_admin", False):
            flash("Admins only.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        form = YearForm()
        set_form = SetCurrentYearForm()

        # Add a new year
        if form.validate_on_submit():
            y = AcademicYear(
                label=form.label.data.strip(),
                is_current=form.is_current.data
            )
            # parse optional dates
            try:
                if form.start_date.data:
                    y.start_date = datetime.strptime(form.start_date.data, "%Y-%m-%d").date()
                if form.end_date.data:
                    y.end_date = datetime.strptime(form.end_date.data, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid date format. Use yyyy-mm-dd.", "error")
                return redirect(url_for("admin_years"))

            if y.is_current:
                # unset others
                AcademicYear.query.update({AcademicYear.is_current: False})
            db.session.add(y)
            db.session.commit()
            flash("Academic year added.", "success")
            return redirect(url_for("admin_years"))

        # Set current year
        if set_form.validate_on_submit() and set_form.submit.data:
            year_id_val = request.form.get("year_id") or set_form.year_id.data
            year = AcademicYear.query.get(int(year_id_val)) if year_id_val else None
            if year:
                AcademicYear.query.update({AcademicYear.is_current: False})
                year.is_current = True
                db.session.commit()
                flash(f"{year.label} set as current.", "success")
            return redirect(url_for("admin_years"))

        years = AcademicYear.query.order_by(AcademicYear.label.asc()).all()
        return render_template("admin_years.html", form=form, set_form=set_form, years=years)

    # ---- CSV import: Results (combined pupils + optional scores)

    @app.route("/import/results", methods=["GET", "POST"])
    @login_required
    def import_results():
        form = CSVUploadResultsForm()
        form.academic_year.choices = academic_year_choices()
        subject = (request.values.get("subject") or form.subject.data or "maths").strip().lower()
        if subject not in ("maths", "reading", "spag", "writing"):
            subject = "maths"
        form.subject.data = subject

        # Admin can choose class; teacher locked to theirs
        if current_user.is_admin:
            form.class_id.choices = class_choices()
        else:
            klass = SchoolClass.query.get(primary_class_id_for(current_user)) if primary_class_id_for(current_user) else None
            label = klass.name if klass else "My class"
            form.class_id.choices = [(primary_class_id_for(current_user), label)]
            form.class_id.data = primary_class_id_for(current_user)

        preview_rows = []

        def normalize_header(header):
            s = re.sub(r"[\s\-]+", "_", str(header or "").strip().lower())
            return re.sub(r"[^a-z0-9_]", "", s)

        header_aliases = {
            "name": ["name", "pupil", "student", "child"],
            "gender": ["gender", "sex"],
            "pupil_premium": ["pp", "pupil_premium", "pupilpremium", "pupil premium", "premium"],
            "laps": ["laps", "lap", "lower_attainers", "lowerattainers"],
            "service_child": ["service", "service_child", "servicechild", "service child"],
            "arithmetic": ["score_a", "scorea", "arithmetic", "paper_a", "papera"],
            "reasoning": ["score_b", "scoreb", "reasoning", "paper_b", "paperb"],
            "note": ["note", "notes", "comment", "comments"],
        }
        normalized_alias_map = {
            normalize_header(alias): canonical
            for canonical, aliases in header_aliases.items()
            for alias in aliases
        }

        def parse_bool(v):
            return str(v or "").strip().lower() in ("1", "true", "yes", "y", "t")

        def parse_gender(v):
            g = (v or "").strip().upper()
            return g if g in ("M", "F") else None

        def parse_float(v):
            if v is None:
                return None
            s = str(v).strip()
            if s == "":
                return None
            return float(s)

        def parse_writing_band(v):
            raw = (v or "").strip().lower()
            if raw in ("",):
                return None
            mapping = {
                "working_towards": "working_towards", "working towards": "working_towards", "wts": "working_towards",
                "working_at": "working_at", "working at": "working_at", "ot": "working_at",
                "exceeding": "exceeding", "gds": "exceeding",
            }
            return mapping.get(raw)

        if form.validate_on_submit():
            upload = request.files.get(form.csv_file.name)
            if not upload or not upload.filename.lower().endswith(".csv"):
                flash("Please upload a .csv file.", "error")
                return redirect(url_for("import_results", subject=subject))

            class_id = form.class_id.data if current_user.is_admin else primary_class_id_for(current_user)
            year_id = form.academic_year.data
            term = form.term.data

            raw = upload.read().decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(raw))
            fieldnames = reader.fieldnames or []
            canonical_fields = tuple(header_aliases.keys())
            field_map = {}
            for original in fieldnames:
                normalized = normalize_header(original)
                canonical = normalized_alias_map.get(normalized)
                if canonical and canonical not in field_map:
                    field_map[canonical] = original

            rows = []
            parsed = []

            for i, r in enumerate(reader, start=1):
                r2 = {canonical: (r.get(field_map.get(canonical, "")) if field_map.get(canonical) else None) for canonical in canonical_fields}

                name = (r2.get("name") or "").strip()
                if not name:
                    rows.append({"row": i, "status": "error", "action": "skip", "name": "", "gender": None, "pp": False, "laps": False, "svc": False, "score_a": None, "score_b": None, "note": None})
                    continue

                gender = parse_gender(r2.get("gender"))
                pp = parse_bool(r2.get("pupil_premium"))
                laps = parse_bool(r2.get("laps"))
                svc = parse_bool(r2.get("service_child"))
                note = (r2.get("note") or "").strip() or None

                try:
                    if subject == "maths":
                        score_a, score_b = parse_float(r2.get("arithmetic")), parse_float(r2.get("reasoning"))
                        band = None
                    elif subject == "reading":
                        score_a, score_b = parse_float(r.get("reading_p1")), parse_float(r.get("reading_p2"))
                        band = None
                    elif subject == "spag":
                        score_a, score_b = parse_float(r.get("spelling")), parse_float(r.get("grammar"))
                        band = None
                    else:
                        score_a = score_b = None
                        band = parse_writing_band(r.get("band") or r.get("writing_band"))
                        if (r.get("band") or r.get("writing_band")) and not band:
                            raise ValueError("Invalid writing band")
                except ValueError:
                    rows.append({"row": i, "status": "error", "action": "bad score", "name": name, "gender": gender, "pp": pp, "laps": laps, "svc": svc, "score_a": None, "score_b": None, "note": note})
                    continue

                pupil = Pupil.query.filter_by(class_id=class_id, name=name).first()
                pupil_action = "update pupil" if pupil else "create pupil"
                result_action = "no result"
                if subject == "writing":
                    if band is not None:
                        existing = WritingResult.query.filter_by(pupil_id=(pupil.id if pupil else -1), academic_year_id=year_id, term=term).first() if pupil else None
                        result_action = "update result" if existing else "create result"
                else:
                    if score_a is not None or score_b is not None:
                        existing = Result.query.filter_by(pupil_id=(pupil.id if pupil else -1), academic_year_id=year_id, term=term, subject=subject).first() if pupil else None
                        result_action = "update result" if existing else "create result"

                rows.append({"row": i, "status": "ok", "action": f"{pupil_action}; {result_action}", "name": name, "gender": gender, "pp": pp, "laps": laps, "svc": svc, "score_a": score_a, "score_b": score_b, "band": band, "note": note})
                parsed.append((name, gender, pp, laps, svc, score_a, score_b, band, note))

            preview_rows = rows

            if form.submit_confirm.data:
                for (name, gender, pp, laps, svc, score_a, score_b, band, note) in parsed:
                    pupil = Pupil.query.filter_by(class_id=class_id, name=name).first()
                    if not pupil:
                        pupil = Pupil(class_id=class_id, name=name, gender=gender, pupil_premium=pp, laps=laps, service_child=svc)
                        db.session.add(pupil)
                        db.session.flush()
                    else:
                        pupil.gender = gender or pupil.gender
                        pupil.pupil_premium = pp
                        pupil.laps = laps
                        pupil.service_child = svc

                    if subject == "writing":
                        if band is None:
                            continue
                        existing = WritingResult.query.filter_by(pupil_id=pupil.id, academic_year_id=year_id, term=term).first()
                        if not existing:
                            existing = WritingResult(pupil_id=pupil.id, academic_year_id=year_id, term=term, band=band, note=note)
                            db.session.add(existing)
                        else:
                            existing.band = band
                            existing.note = note
                    else:
                        if score_a is None and score_b is None:
                            continue
                        existing = Result.query.filter_by(pupil_id=pupil.id, academic_year_id=year_id, term=term, subject=subject).first()
                        if not existing:
                            existing = Result(pupil_id=pupil.id, academic_year_id=year_id, term=term, class_id_snapshot=pupil.class_id, subject=subject)
                            db.session.add(existing)

                        set_result_scores(existing, subject, score_a, score_b)
                        try:
                            combined_pct, summary = compute_combined_and_band(score_a, score_b, pupil.class_id, term, year_id, subject)
                        except Exception:
                            combined_pct, summary = None, None
                        existing.combined_pct = combined_pct
                        existing.summary = summary
                        existing.note = note
                        existing.class_id_snapshot = pupil.class_id

                db.session.commit()
                flash("Saved.", "success")
                return redirect(url_for("dashboard", subject=subject, year=year_id, **{"class": class_id}))

            return render_template("import_results.html", form=form, preview_rows=preview_rows, selected_subject=subject)

        return render_template("import_results.html", form=form, preview_rows=preview_rows, selected_subject=subject)

    # ---- GAP Analysis

    @app.route("/assessments")
    @login_required
    def assessments():
        ensure_default_year()
        subject = normalize_subject(request.args.get("subject", "maths"))
        q = Assessment.query.filter_by(subject=subject)
        # Admin: see all; Teacher: see their class only
        if not getattr(current_user, "is_admin", False):
            q = q.filter(Assessment.class_id.in_(user_class_ids(current_user)))
        items = q.order_by(Assessment.created_at.desc()).all()
        return render_template("assessments.html", assessments=items, subject=subject, subjects=SUBJECTS)

    @app.route("/assessments/new", methods=["GET", "POST"])
    @login_required
    def assessment_new():
        flash("Manual assessment creation disabled", "error")
        return redirect(url_for("assessments"))

    @app.route("/assessments/<int:assessment_id>/questions", methods=["GET", "POST"])
    @login_required
    def assessment_questions(assessment_id):
        a = Assessment.query.get_or_404(assessment_id)
        require_class_access(a.class_id)

        if request.method == "POST":
            qns = request.form.getlist("qn[]")
            maxs = request.form.getlist("max[]")
            strands = request.form.getlist("strand[]")
            qtypes = request.form.getlist("qtype[]")
            notes = request.form.getlist("notes[]")

            AssessmentQuestion.query.filter_by(assessment_id=a.id).delete()
            entries = []
            for idx, (qn, mx, st) in enumerate(zip(qns, maxs, strands)):
                try:
                    qn_i = int(qn)
                    mx_f = float(mx) if mx else 1.0
                    st_v = (st or "").strip() or None
                    qt_v = (qtypes[idx] if idx < len(qtypes) else "").strip() or None
                    nt_v = (notes[idx] if idx < len(notes) else "").strip() or None
                except ValueError:
                    continue
                entries.append((qn_i, mx_f, st_v, qt_v, nt_v))

            entries.sort(key=lambda t: t[0])
            for i, (qn_i, mx_f, st_v, qt_v, nt_v) in enumerate(entries, start=1):
                db.session.add(
                    AssessmentQuestion(assessment_id=a.id, number=i, max_mark=mx_f, strand=st_v, question_type=qt_v, notes=nt_v)
                )
            db.session.flush()

            # Align total marks with configured maximum for this subject/paper
            required_total = get_term_max_for_paper(a.class_id, a.academic_year_id, a.term, a.subject, a.paper)
            ensure_questions_total_marks(a.id, required_total)

            db.session.commit()
            flash(f"Questions saved (total marks aligned to {int(required_total)}).", "success")
            return redirect(url_for("assessment_scores", assessment_id=a.id))

        qs = AssessmentQuestion.query.filter_by(assessment_id=a.id).order_by(AssessmentQuestion.number.asc()).all()
        return render_template("assessment_questions.html", assessment=a, questions=qs)

    @app.route("/assessments/<int:assessment_id>/scores", methods=["GET", "POST"])
    @login_required
    def assessment_scores(assessment_id):
        a = Assessment.query.get_or_404(assessment_id)
        require_class_access(a.class_id)

        pupils = Pupil.query.filter_by(class_id=a.class_id).order_by(
            Pupil.number.is_(None), Pupil.number, Pupil.name
        ).all()
        questions = AssessmentQuestion.query.filter_by(assessment_id=a.id).order_by(
            AssessmentQuestion.number.asc()
        ).all()

        if request.method == "POST":
            for p in pupils:
                for q in questions:
                    field_name = f"score_{p.id}_{q.id}"
                    val = request.form.get(field_name, "")
                    try:
                        mark = float(val) if val != "" else 0.0
                    except ValueError:
                        mark = 0.0
                    s = PupilQuestionScore.query.filter_by(
                        assessment_id=a.id, pupil_id=p.id, question_id=q.id
                    ).first()
                    if s:
                        s.mark = mark
                        s.updated_by_teacher_id = current_user.id
                    else:
                        db.session.add(PupilQuestionScore(
                            assessment_id=a.id, pupil_id=p.id, question_id=q.id, mark=mark,
                            updated_by_teacher_id=current_user.id
                        ))
            # Save question-level marks
            db.session.commit()

            # Push totals into Results so dashboard + pupil report update
            sync_assessment_totals_to_results(a.id)

            flash("Scores saved.", "success")
            return redirect(url_for("assessment_analysis", assessment_id=a.id))

        scores = {(s.pupil_id, s.question_id): s.mark for s in PupilQuestionScore.query.filter_by(assessment_id=a.id).all()}
        return render_template("assessment_scores.html", assessment=a, pupils=pupils, questions=questions, scores=scores)

    @app.route("/assessments/<int:assessment_id>/analysis")
    @login_required
    def assessment_analysis(assessment_id):
        a = Assessment.query.get_or_404(assessment_id)
        require_class_access(a.class_id)

        questions = AssessmentQuestion.query.filter_by(assessment_id=a.id).order_by(AssessmentQuestion.number.asc()).all()
        q_by_id = {q.id: q for q in questions}

        pupils = Pupil.query.filter_by(class_id=a.class_id).order_by(Pupil.number.is_(None), Pupil.number, Pupil.name).all()
        scores = PupilQuestionScore.query.filter_by(assessment_id=a.id).all()

        per_pupil_total = {p.id: {"mark": 0.0, "max": 0.0} for p in pupils}
        per_pupil_strand = {p.id: {} for p in pupils}

        for s in scores:
            q = q_by_id.get(s.question_id)
            if not q:
                continue
            bucket = per_pupil_total[s.pupil_id]
            bucket["mark"] += s.mark
            bucket["max"] += q.max_mark

            strand = q.question_type or q.strand or "General"
            sb = per_pupil_strand[s.pupil_id].setdefault(strand, {"mark": 0.0, "max": 0.0})
            sb["mark"] += s.mark
            sb["max"] += q.max_mark

        flagged = []
        for p in pupils:
            t = per_pupil_total[p.id]
            overall_pct = round((t["mark"] / t["max"]) * 100.0, 1) if t["max"] > 0 else 0.0
            reasons = []
            if overall_pct < 50.0:
                reasons.append(f"Low overall score ({overall_pct}%).")
            weak_strands = []
            for strand, v in per_pupil_strand[p.id].items():
                pct = (v["mark"] / v["max"]) * 100.0 if v["max"] > 0 else 0.0
                if pct < 40.0:
                    weak_strands.append((strand, round(pct, 1)))
            weak_strands.sort(key=lambda x: x[1])
            for strand, pct in weak_strands[:2]:
                reasons.append(f"Weak in {strand} ({pct}%).")
            if reasons:
                flagged.append({"pupil": p, "overall_pct": overall_pct, "reasons": reasons})

        data = []
        for q in questions:
            agg = db.session.query(func.avg(PupilQuestionScore.mark)).filter_by(question_id=q.id).scalar() or 0.0
            pct = round((agg / q.max_mark) * 100.0, 1) if q.max_mark > 0 else 0.0
            data.append({"number": q.number, "avg_pct": pct, "strand": q.question_type or q.strand or ""})

        return render_template("assessment_analysis.html", assessment=a, data=data, flagged=flagged)

    @app.route("/gap/templates")
    @login_required
    def gap_templates():
        subject = normalize_subject(request.args.get("subject", "maths"))
        year = parse_year_id_or_current(request.args.get("year"))
        term = request.args.get("term") or "Autumn"
        year_group = int(request.args.get("year_group") or ((SchoolClass.query.get(primary_class_id_for(current_user)).year_group if (not is_admin_user() and primary_class_id_for(current_user)) else 6) or 6))
        q = PaperTemplate.query.filter_by(subject=subject, academic_year_id=year.id, term=term, year_group=year_group).order_by(PaperTemplate.paper.asc(), PaperTemplate.version.desc())
        return render_template("gap_templates.html", items=q.all(), subject=subject, term=term, year=year, year_group=year_group, subjects=SUBJECTS)

    @app.route("/gap/templates/new", methods=["GET", "POST"])
    @login_required
    def gap_template_new():
        years = AcademicYear.query.order_by(AcademicYear.label.asc()).all()
        if request.method == "POST":
            subject = normalize_subject(request.form.get("subject", "maths"))
            paper = (request.form.get("paper") or "").strip()
            term = (request.form.get("term") or "Autumn").strip()
            year_group = int(request.form.get("year_group") or 6)
            year_id = int(request.form.get("academic_year_id") or 0)
            title = (request.form.get("title") or "").strip() or None
            copy_from_id = int(request.form.get("copy_from_id") or 0)
            if paper not in PAPERS[subject] or term not in TERMS:
                flash("Invalid template options.", "error")
                return redirect(url_for("gap_template_new"))
            prev = PaperTemplate.query.get(copy_from_id) if copy_from_id else None
            next_version = 1 + (db.session.query(func.max(PaperTemplate.version)).filter_by(subject=subject, paper=paper, academic_year_id=year_id, year_group=year_group, term=term).scalar() or 0)
            t = PaperTemplate(subject=subject, paper=paper, academic_year_id=year_id, year_group=year_group, term=term, title=title, version=next_version, is_active=True)
            db.session.add(t)
            db.session.flush()
            if prev:
                src = PaperTemplateQuestion.query.filter_by(template_id=prev.id).order_by(PaperTemplateQuestion.number.asc()).all()
                for q in src:
                    db.session.add(PaperTemplateQuestion(template_id=t.id, number=q.number, max_mark=q.max_mark, question_type=q.question_type, notes=q.notes, strand=q.strand))
            PaperTemplate.query.filter(PaperTemplate.id != t.id, PaperTemplate.subject == subject, PaperTemplate.paper == paper, PaperTemplate.academic_year_id == year_id, PaperTemplate.year_group == year_group, PaperTemplate.term == term).update({PaperTemplate.is_active: False})
            db.session.commit()
            return redirect(url_for("gap_template_edit", template_id=t.id))
        template_choices = PaperTemplate.query.order_by(PaperTemplate.created_at.desc()).limit(30).all()
        return render_template("gap_template_new.html", years=years, papers=PAPERS, subjects=SUBJECTS, terms=TERMS, template_choices=template_choices)

    @app.route("/gap/templates/<int:template_id>", methods=["GET", "POST"])
    @login_required
    def gap_template_edit(template_id):
        t = PaperTemplate.query.get_or_404(template_id)
        if request.method == "POST":
            nums = request.form.getlist("qn[]")
            maxs = request.form.getlist("max[]")
            types = request.form.getlist("qtype[]")
            notes = request.form.getlist("notes[]")
            strands = request.form.getlist("strand[]")
            PaperTemplateQuestion.query.filter_by(template_id=t.id).delete()
            for i, raw in enumerate(nums):
                try:
                    n = int(raw)
                    m = float(maxs[i] or 0)
                except Exception:
                    continue
                db.session.add(PaperTemplateQuestion(template_id=t.id, number=n, max_mark=m, question_type=(types[i] or '').strip() or None, notes=(notes[i] or '').strip() or None, strand=(strands[i] or '').strip() or None))
            t.updated_at = datetime.utcnow()
            db.session.commit()
            flash("Template saved.", "success")
            return redirect(url_for("gap_template_edit", template_id=t.id))
        questions = PaperTemplateQuestion.query.filter_by(template_id=t.id).order_by(PaperTemplateQuestion.number.asc()).all()
        return render_template("gap_template_edit.html", template=t, questions=questions)

    @app.post("/gap/templates/<int:template_id>/publish")
    @login_required
    def gap_template_publish(template_id):
        t = PaperTemplate.query.get_or_404(template_id)
        PaperTemplate.query.filter(PaperTemplate.id != t.id, PaperTemplate.subject == t.subject, PaperTemplate.paper == t.paper, PaperTemplate.academic_year_id == t.academic_year_id, PaperTemplate.year_group == t.year_group, PaperTemplate.term == t.term).update({PaperTemplate.is_active: False})
        t.is_active = True
        db.session.commit()
        flash("Template published as active.", "success")
        return redirect(url_for("gap_template_edit", template_id=t.id))

    @app.post("/gap/templates/<int:template_id>/new_version")
    @login_required
    def gap_template_new_version(template_id):
        t = PaperTemplate.query.get_or_404(template_id)
        new_t = clone_template(t, make_active=True)
        db.session.commit()
        flash("New version created and set active.", "success")
        return redirect(url_for("gap_template_edit", template_id=new_t.id))

    @app.post("/gap/templates/<int:template_id>/copy_to_next_year")
    @login_required
    def gap_template_copy_next_year(template_id):
        t = PaperTemplate.query.get_or_404(template_id)
        ny_label = next_year_label_from(t.academic_year.label)
        ny = AcademicYear.query.filter_by(label=ny_label).first()
        if not ny:
            ny = AcademicYear(label=ny_label, is_current=False)
            db.session.add(ny)
            db.session.flush()
        next_version = 1 + (db.session.query(func.max(PaperTemplate.version)).filter_by(subject=t.subject, paper=t.paper, academic_year_id=ny.id, year_group=t.year_group, term=t.term).scalar() or 0)
        cp = PaperTemplate(subject=t.subject, paper=t.paper, academic_year_id=ny.id, year_group=t.year_group, term=t.term, title=t.title, version=next_version, is_active=True)
        db.session.add(cp)
        db.session.flush()
        for q in PaperTemplateQuestion.query.filter_by(template_id=t.id).order_by(PaperTemplateQuestion.number.asc()).all():
            db.session.add(PaperTemplateQuestion(template_id=cp.id, number=q.number, max_mark=q.max_mark, question_type=q.question_type, notes=q.notes, strand=q.strand))
        PaperTemplate.query.filter(PaperTemplate.id != cp.id, PaperTemplate.subject == cp.subject, PaperTemplate.paper == cp.paper, PaperTemplate.academic_year_id == cp.academic_year_id, PaperTemplate.year_group == cp.year_group, PaperTemplate.term == cp.term).update({PaperTemplate.is_active: False})
        db.session.commit()
        flash("Template copied to next academic year.", "success")
        return redirect(url_for("gap_template_edit", template_id=cp.id))

    # ---- Interventions: propose (teacher/admin — from red flag next to GAP link)

    @app.route("/interventions/propose", methods=["GET", "POST"])
    @login_required
    def propose_interventions():
        try:
            class_id = int(request.args.get("class_id", 0) or request.form.get("class_id", 0))
        except ValueError:
            class_id = 0
        try:
            year_id = int(request.args.get("year_id", 0) or request.form.get("year_id", 0))
        except ValueError:
            year_id = 0
        term = (request.args.get("term") or request.form.get("term") or "").strip()
        subject = (request.args.get("subject") or request.form.get("subject") or "maths").strip().lower()
        paper = (request.args.get("paper") or request.form.get("paper") or "").strip()

        if subject not in SUBJECTS or term not in TERMS or paper not in PAPERS[subject]:
            flash("Invalid term/subject/paper.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        require_class_access(class_id)
        klass = SchoolClass.query.get_or_404(class_id)
        if klass.is_archived or klass.is_archive:
            flash("Cannot propose interventions for archived pupils.", "error")
            return redirect(url_for("dashboard", subject=subject))
        year = AcademicYear.query.get_or_404(year_id)

        pupils = Pupil.query.filter_by(class_id=class_id).order_by(Pupil.number.is_(None), Pupil.number, Pupil.name).all()
        max_marks = get_term_max_for_paper(class_id, year_id, term, subject, paper)
        field = result_field_for_paper(subject, paper)

        existing_items = Intervention.query.filter_by(class_id=class_id, academic_year_id=year_id, term=term, paper=paper).all()
        existing_by_pupil = {it.pupil_id: it for it in existing_items}

        data = []
        for p in pupils:
            r = (Result.query.filter_by(pupil_id=p.id, academic_year_id=year_id, term=term, subject=subject)
                 .order_by(Result.created_at.desc()).first())
            if not r:
                continue
            raw = getattr(r, field)
            if raw is None or max_marks <= 0:
                continue
            pct = round((raw / max_marks) * 100.0, 1)
            existing = existing_by_pupil.get(p.id)
            row = {"pupil": p, "pct": pct, "existing": existing, "focus": ""}
            if existing and existing.focus_areas:
                try:
                    row["focus"] = ", ".join(json.loads(existing.focus_areas))
                except Exception:
                    row["focus"] = existing.focus_areas
            if pct < 55.0 or existing:
                data.append(row)

        data.sort(key=lambda x: (55.0 - x["pct"], x["pupil"].name))
        pre_ids = [d["pupil"].id for d in data[:6]]

        if request.method == "POST":
            selected_ids = set(int(x) for x in request.form.getlist("selected[]"))
            created = 0
            for d in data:
                pid = d["pupil"].id
                itm = existing_by_pupil.get(pid)
                teacher_note = (request.form.get(f"teacher_note_{pid}") or "").strip() or None
                if pid in selected_ids:
                    if not itm:
                        itm = Intervention(
                            pupil_id=pid, class_id=class_id,
                            academic_year_id=year_id, term=term, paper=paper,
                            pct=d["pct"], status="proposed", selected_by=current_user.id
                        )
                        db.session.add(itm)
                        created += 1
                    else:
                        itm.pct = d["pct"]
                        itm.status = "proposed"
                        itm.selected_by = current_user.id
                    itm.teacher_note = teacher_note
                    itm.teacher_updated_at = datetime.utcnow()
                    recompute_focus_areas_for_intervention(itm)
            db.session.commit()
            flash(f"Saved {created} interventions ({paper}, {term}, {year.label}).", "success")
            return redirect(url_for("dashboard", subject=subject, year=year_id, **{"class": class_id}))

        return render_template(
            "interventions_propose.html",
            klass=klass, year=year, term=term, paper=paper,
            rows=data, pre_ids=pre_ids, subject=subject,
        )

    # ---- Unified interventions page (teacher + admin)

    @app.route("/interventions", methods=["GET", "POST"])
    @login_required
    def interventions():
        year = parse_year_id_or_current(request.args.get("year"))
        year_id = year.id if year else None
        term = (request.args.get("term") or "").strip()
        subject = (request.args.get("subject") or "").strip().lower()
        status = (request.args.get("status") or "").strip().lower()
        group_name = (request.form.get("group") or request.args.get("group") or "").strip()
        pp = request.args.get("pp", "")
        include_pupil_ids = parse_id_csv(request.args.get("pupil_ids"))

        requested_class = request.args.get("class", "")
        class_id = 0
        try:
            class_id = int(requested_class or 0)
        except ValueError:
            class_id = 0

        is_admin = bool(getattr(current_user, "is_admin", False))
        if not is_admin:
            class_id = primary_class_id_for(current_user) or 0

        q = Intervention.query.join(Pupil, Pupil.id == Intervention.pupil_id).join(SchoolClass, SchoolClass.id == Intervention.class_id)
        if year_id:
            q = q.filter(Intervention.academic_year_id == year_id)
        if class_id:
            require_class_access(class_id)
            q = q.filter(Intervention.class_id == class_id)
        elif not is_admin:
            q = q.filter(Intervention.class_id == (primary_class_id_for(current_user) or 0))

        if term in TERMS:
            q = q.filter(Intervention.term == term)
        if subject in SUBJECTS:
            q = q.filter(Intervention.paper.in_(PAPERS[subject]))
        elif subject == "writing":
            q = q.filter(Intervention.paper == "Writing")
        if status in ("proposed", "active", "closed"):
            q = q.filter(Intervention.status == status)
        elif status == "awaiting-post":
            q = q.filter(Intervention.status == "closed", ((Intervention.post_result.is_(None)) | (Intervention.post_result == "")))
        if pp == "1":
            q = q.filter(Pupil.pupil_premium.is_(True))
        elif pp == "0":
            q = q.filter(Pupil.pupil_premium.is_(False))
        if include_pupil_ids:
            q = q.filter(Intervention.pupil_id.in_(include_pupil_ids))
        if group_name:
            q = q.filter(Intervention.focus_areas.ilike(f"%{group_name}%"))
        if lead:
            try:
                q = q.filter(Intervention.selected_by == int(lead))
            except ValueError:
                pass
        if year_group:
            try:
                q = q.filter(SchoolClass.year_group == int(year_group))
            except ValueError:
                pass

        items = q.order_by(SchoolClass.name.asc(), Pupil.name.asc(), Intervention.created_at.desc()).all() if is_admin else q.order_by(Pupil.name.asc(), Intervention.created_at.desc()).all()
        years = AcademicYear.query.order_by(AcademicYear.label.asc()).all()
        classes = active_classes_query().order_by(SchoolClass.name.asc()).all()
        add_pupils = []
        if is_admin:
            if class_id:
                add_pupils = Pupil.query.filter_by(class_id=class_id).order_by(Pupil.number.is_(None), Pupil.number, Pupil.name).all()
        else:
            add_pupils = Pupil.query.filter_by(class_id=class_id).order_by(Pupil.number.is_(None), Pupil.number, Pupil.name).all()

        if request.method == "POST":
            action = (request.form.get("action") or "save").strip().lower()
            if action == "add":
                try:
                    pupil_id = int(request.form.get("pupil_id") or 0)
                except ValueError:
                    pupil_id = 0
                pupil = Pupil.query.get_or_404(pupil_id)
                require_pupil_access(pupil)
                if is_admin and not class_id:
                    flash("Select a class before adding an intervention.", "error")
                    return redirect(url_for("interventions", year=year_id, subject=subject or "", term=term or "", status=status or "", pp=pp or ""))

                add_subject = (request.form.get("add_subject") or "maths").strip().lower()
                add_term = (request.form.get("add_term") or term or TERMS[0]).strip()
                if add_subject not in (*SUBJECTS, "writing"):
                    add_subject = "maths"
                if add_term not in TERMS:
                    add_term = TERMS[0]
                add_focus = (request.form.get("add_focus") or "").strip() or None
                add_support = (request.form.get("add_support") or "").strip() or None
                add_pre = (request.form.get("add_pre") or "").strip() or None

                paper = "Writing" if add_subject == "writing" else PAPERS[add_subject][0]
                it = Intervention.query.filter_by(
                    pupil_id=pupil.id,
                    class_id=pupil.class_id,
                    academic_year_id=year_id,
                    term=add_term,
                    paper=paper,
                ).first()
                if not it:
                    it = Intervention(
                        pupil_id=pupil.id,
                        class_id=pupil.class_id,
                        academic_year_id=year_id,
                        term=add_term,
                        paper=paper,
                        status="proposed",
                        selected_by=current_user.id,
                    )
                    db.session.add(it)
                it.focus_areas = add_focus
                it.support_plan = add_support
                it.pre_result = add_pre
                it.teacher_updated_at = datetime.utcnow()
                db.session.commit()
                flash("Intervention added.", "success")
            else:
                allowed_status = {"proposed", "active", "closed"}
                for it in items:
                    it.focus_areas = (request.form.get(f"focus_{it.id}", "").strip() or None)
                    it.support_plan = (request.form.get(f"support_{it.id}", "").strip() or None)
                    it.pre_result = (request.form.get(f"pre_{it.id}", "").strip() or None)
                    it.post_result = (request.form.get(f"post_{it.id}", "").strip() or None)
                    it.teacher_note = (request.form.get(f"teacher_note_{it.id}", "").strip() or None)
                    st = (request.form.get(f"status_{it.id}", "").strip() or it.status).lower()
                    if st in allowed_status:
                        it.status = st
                    if hasattr(it, "review_due_date"):
                        raw_due = (request.form.get(f"review_due_{it.id}", "").strip())
                        it.review_due_date = date.fromisoformat(raw_due) if raw_due else None
                    it.teacher_updated_at = datetime.utcnow()
                db.session.commit()
                flash("Interventions updated.", "success")

            params = {"year": year_id}
            if is_admin and class_id:
                params["class"] = class_id
            if term:
                params["term"] = term
            if subject:
                params["subject"] = subject
            if status:
                params["status"] = status
            if pp:
                params["pp"] = pp
            if lead:
                params["lead"] = lead
            if group_name:
                params["group"] = group_name
            if year_group:
                params["year_group"] = year_group
            return redirect(url_for("interventions", **params))

        show_review_due_date = "review_due_date" in {c["name"] for c in inspect(db.engine).get_columns("interventions")}

        filter_params = {"year": year_id}
        if is_admin and class_id:
            filter_params["class"] = class_id
        if term:
            filter_params["term"] = term
        if subject:
            filter_params["subject"] = subject
        if status:
            filter_params["status"] = status
        if pp:
            filter_params["pp"] = pp
        if lead:
            filter_params["lead"] = lead
        if group_name:
            filter_params["group"] = group_name
        if year_group:
            filter_params["year_group"] = year_group

        leads = Teacher.query.order_by(Teacher.username.asc()).all()
        avg_impact_values = [it.impact for it in items if it.impact is not None]
        interventions_summary = {
            "avg_impact": round(sum(avg_impact_values) / len(avg_impact_values), 2) if avg_impact_values else None,
            "completed": sum(1 for it in items if it.status == "closed"),
            "awaiting_post": sum(1 for it in items if it.status == "closed" and it.post_score_value is None),
        }

        return render_template(
            "interventions.html",
            items=items,
            years=years,
            classes=classes,
            add_pupils=add_pupils,
            year=year,
            sel_class=class_id,
            sel_term=term,
            sel_subject=subject,
            sel_status=status,
            sel_pp=pp,
            sel_lead=lead,
            sel_group_name=group_name,
            sel_year_group=year_group,
            is_admin=is_admin,
            leads=leads,
            interventions_summary=interventions_summary,
            show_review_due_date=show_review_due_date,
            filter_params=filter_params,
        )

    @app.route("/admin/interventions", methods=["GET", "POST"])
    @login_required
    def admin_interventions():
        return redirect(url_for("interventions", **request.args.to_dict(flat=True)))

    @app.route("/reports/parent-summary/<int:pupil_id>", methods=["GET", "POST"])
    @login_required
    def report_parent_summary(pupil_id):
        pupil = Pupil.query.get_or_404(pupil_id)
        require_pupil_access(pupil)
        year = parse_year_id_or_current(request.args.get("year"))
        term = (request.args.get("term") or "Summer").strip()
        if term not in TERMS:
            term = "Summer"

        note = PupilReportNote.query.filter_by(pupil_id=pupil.id, year_id=year.id, term_id=term).first()
        if request.method == "POST":
            strengths_text = (request.form.get("strengths_text") or "").strip() or None
            next_steps_text = (request.form.get("next_steps_text") or "").strip() or None
            if not note:
                note = PupilReportNote(pupil_id=pupil.id, year_id=year.id, term_id=term)
                db.session.add(note)
            note.strengths_text = strengths_text
            note.next_steps_text = next_steps_text
            note.updated_by = current_user.id
            note.updated_at = datetime.utcnow()
            db.session.commit()
            flash("Parent summary notes saved.", "success")
            return redirect(url_for("report_parent_summary", pupil_id=pupil.id, year=year.id, term=term))

        latest = {}
        for subject_key in SUBJECTS:
            row = (Result.query
                   .filter_by(pupil_id=pupil.id, academic_year_id=year.id, term=term, subject=subject_key)
                   .order_by(Result.created_at.desc())
                   .first())
            latest[subject_key] = row
        writing = (WritingResult.query
                   .filter_by(pupil_id=pupil.id, academic_year_id=year.id, term=term)
                   .order_by(WritingResult.created_at.desc())
                   .first())

        format_mode = (request.args.get("format") or "html").strip().lower()
        html = render_template(
            "reports/parent_summary.html",
            pupil=pupil,
            year=year,
            term=term,
            latest=latest,
            writing=writing,
            note=note,
            is_pdf=(format_mode == "pdf"),
        )
        if format_mode == "pdf":
            if HTML is None:
                flash("WeasyPrint is not installed. Showing HTML preview instead.", "error")
                return render_template(
                    "reports/parent_summary.html",
                    pupil=pupil,
                    year=year,
                    term=term,
                    latest=latest,
                    writing=writing,
                    note=note,
                    is_pdf=False,
                )
            pdf_bytes = HTML(string=html, base_url=request.host_url).write_pdf()
            resp = make_response(pdf_bytes)
            resp.headers["Content-Type"] = "application/pdf"
            resp.headers["Content-Disposition"] = f"attachment; filename=parent-summary-{pupil.id}.pdf"
            return resp
        return html

    @app.route("/reports/pupil/<int:pupil_id>")
    @login_required
    def report_pupil(pupil_id):
        pupil = Pupil.query.get_or_404(pupil_id)
        require_pupil_access(pupil)
        year = parse_year_id_or_current(request.args.get("year"))
        subject = (request.args.get("subject") or "maths").strip().lower()
        if subject not in (*SUBJECTS, "writing"):
            subject = "maths"

        results = Result.query.filter_by(pupil_id=pupil.id, academic_year_id=year.id).all()
        writing = WritingResult.query.filter_by(pupil_id=pupil.id, academic_year_id=year.id).all()
        interventions = Intervention.query.filter_by(pupil_id=pupil.id, academic_year_id=year.id).order_by(Intervention.created_at.desc()).all()

        by_term = {t: {"core": {}, "combined": None, "band": None} for t in TERMS}
        for r in results:
            bucket = by_term.get(r.term)
            if not bucket:
                continue
            bucket["core"][r.subject] = {"a": r.arithmetic if r.subject == 'maths' else (r.reading_p1 if r.subject == 'reading' else r.spelling), "b": r.reasoning if r.subject == 'maths' else (r.reading_p2 if r.subject == 'reading' else r.grammar), "combined": r.combined_pct, "band": r.summary}
        for w in writing:
            if w.term in by_term:
                by_term[w.term]["writing_band"] = writing_band_label(w.band)

        return render_template("report_pupil.html", pupil=pupil, year=year, by_term=by_term, interventions=interventions, subject=subject, terms=TERMS)

    @app.route("/reports/class/<int:class_id>")
    @login_required
    def report_class(class_id):
        require_class_access(class_id)
        klass = SchoolClass.query.get_or_404(class_id)
        year = parse_year_id_or_current(request.args.get("year"))
        subject = (request.args.get("subject") or "maths").strip().lower()
        term = (request.args.get("term") or "").strip()
        if subject not in SUBJECTS:
            subject = "maths"

        pupils = Pupil.query.filter_by(class_id=class_id).order_by(Pupil.number.is_(None), Pupil.number, Pupil.name).all()
        pupil_ids = [p.id for p in pupils]
        rq = Result.query.filter(Result.pupil_id.in_(pupil_ids), Result.academic_year_id == year.id, Result.subject == subject)
        if term in TERMS:
            rq = rq.filter(Result.term == term)
        results = rq.all()
        by_key = {(r.pupil_id, r.term): r for r in results}
        interventions = Intervention.query.filter_by(class_id=class_id, academic_year_id=year.id).order_by(Intervention.created_at.desc()).all()
        return render_template("report_class.html", klass=klass, year=year, pupils=pupils, by_key=by_key, terms=TERMS, subject=subject, interventions=interventions, term=term)

    @app.route("/reports")
    @login_required
    def reports():
        dataset = build_report_dataset(request.args, current_user)
        years = AcademicYear.query.order_by(AcademicYear.label.asc()).all()

        query_pairs = []
        for key in ["subject", "year", "class", "gender", "pp", "laps", "svc", "min_pct", "max_pct", "band"]:
            val = request.args.get(key)
            if val not in (None, ""):
                query_pairs.append((key, val))
        query_string = urlencode(query_pairs)

        return render_template(
            "reports.html",
            years=years,
            subjects=SUBJECTS + ("writing",),
            terms=TERMS,
            dataset=dataset,
            query_string=query_string,
        )

    @app.route("/reports/pdf")
    @login_required
    def reports_pdf():
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.lib.units import mm
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet
        except ImportError:
            flash("PDF export dependency is missing (reportlab).", "error")
            return redirect(url_for("reports", **request.args.to_dict()))

        dataset = build_report_dataset(request.args, current_user)
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm, topMargin=10 * mm, bottomMargin=10 * mm)
        styles = getSampleStyleSheet()

        class_label = "All classes" if dataset.get("all_classes") else (dataset.get("selected_class").name if dataset.get("selected_class") else "")
        title = f"Report — {dataset.get('subject_label')}"
        meta = f"Class: {class_label} | Year: {dataset.get('year').label if dataset.get('year') else ''} | Date: {datetime.utcnow().strftime('%Y-%m-%d')}"

        elements = [
            Paragraph(title, styles["Heading2"]),
            Paragraph(meta, styles["Normal"]),
            Paragraph(f"Filters: {dataset.get('filter_summary')}", styles["Normal"]),
            Spacer(1, 8),
        ]

        table_data = [dataset.get("headers", [])]
        for row in dataset.get("rows", []):
            table_data.append([str(row.get(h, "")) for h in dataset.get("headers", [])])

        if len(table_data) == 1:
            table_data.append(["No matching pupils"] + [""] * (len(dataset.get("headers", [])) - 1))

        widths = []
        for h in dataset.get("headers", []):
            if h == "Name":
                widths.append(55 * mm)
            elif h in ("Autumn", "Spring", "Summer"):
                widths.append(45 * mm)
            else:
                widths.append(22 * mm)

        table = Table(table_data, repeatRows=1, colWidths=widths)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(table)
        doc.build(elements)
        buffer.seek(0)

        return send_file(buffer, as_attachment=True, download_name="report.pdf", mimetype="application/pdf")

    @app.route("/reports/xlsx")
    @login_required
    def reports_xlsx():
        try:
            from openpyxl import Workbook
        except ImportError:
            flash("Excel export dependency is missing (openpyxl).", "error")
            return redirect(url_for("reports", **request.args.to_dict()))

        dataset = build_report_dataset(request.args, current_user)
        wb = Workbook()
        ws = wb.active
        ws.title = "Results"

        headers = dataset.get("headers", [])
        ws.append(headers)
        for row in dataset.get("rows", []):
            ws.append([row.get(h, "") for h in headers])

        ws.freeze_panes = "A2"
        for idx, h in enumerate(headers, start=1):
            width = 14
            if h == "Name":
                width = 24
            elif h in ("Autumn", "Spring", "Summer"):
                width = 30
            ws.column_dimensions[chr(64 + idx)].width = width

        out = io.BytesIO()
        wb.save(out)
        out.seek(0)
        return send_file(out, as_attachment=True, download_name="report.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ---- Admin: Home overview by class with per-subject charts

    @app.route("/admin/home", endpoint="admin_home")
    @app.route("/admin/overview", endpoint="admin_overview")
    @login_required
    def admin_home():
        if not getattr(current_user, "is_admin", False):
            flash("Admins only.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        years = AcademicYear.query.order_by(AcademicYear.label.asc()).all()
        current_year = get_current_year()

        try:
            year_id = int(request.args.get("year") or (current_year.id if current_year else 0))
        except (TypeError, ValueError):
            year_id = current_year.id if current_year else 0

        term = (request.args.get("term") or "Autumn").strip()
        if term not in TERMS:
            term = "Autumn"

        gender = (request.args.get("gender") or "").upper()
        pp = request.args.get("pp", "")
        include_pupil_ids = parse_id_csv(request.args.get("pupil_ids"))
        laps = request.args.get("laps", "")
        svc = request.args.get("svc", "")

        classes = active_classes_query().order_by(SchoolClass.name.asc()).all()
        class_cards = []
        for klass in classes:
            pq = Pupil.query.filter(Pupil.class_id == klass.id)
            pq = apply_group_filters(pq, gender=gender, pp=pp, laps=laps, svc=svc)
            pupil_ids = [p.id for p in pq.all()]

            subjects_data = []
            for subject_key in ("maths", "reading", "spag", "writing"):
                distribution = subject_distribution_for_pupil_ids(pupil_ids, year_id, term, subject_key)
                table_params = {
                    "mode": "table",
                    "subject": subject_key,
                    "class": klass.id,
                    "year": year_id,
                    "term": term,
                    "gender": gender,
                    "pp": pp,
                    "laps": laps,
                    "svc": svc,
                }
                subjects_data.append({
                    "key": subject_key,
                    "label": "Maths" if subject_key == "maths" else ("Reading" if subject_key == "reading" else ("SPaG" if subject_key == "spag" else "Writing")),
                    "chart_id": f"class-{klass.id}-{subject_key}",
                    "table_url": url_for("dashboard", **table_params),
                    **distribution,
                })

            class_cards.append({
                "id": klass.id,
                "name": klass.name,
                "year_group": klass.year_group,
                "subjects": subjects_data,
            })

        return render_template(
            "admin_overview.html",
            years=years,
            sel_year_id=year_id,
            sel_term=term,
            sel_gender=gender,
            sel_pp=pp,
            sel_laps=laps,
            sel_svc=svc,
            class_cards=class_cards,
        )

    @app.route("/admin/pupils_overview")
    @login_required
    def admin_pupils_overview():
        if not is_admin_user():
            flash("Admins only.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        years = AcademicYear.query.order_by(AcademicYear.label.asc()).all()
        current_year = get_current_year()
        try:
            year_id = int(request.args.get("year") or (current_year.id if current_year else 0))
        except (TypeError, ValueError):
            year_id = current_year.id if current_year else 0

        year_group_filter = (request.args.get("year_group") or "").strip()
        class_filter = (request.args.get("class") or "").strip()
        pp_filter = (request.args.get("pp") or "").strip()
        laps_filter = (request.args.get("laps") or "").strip()
        service_filter = (request.args.get("service") or "").strip()
        send_filter = (request.args.get("send") or "").strip()
        ehcp_filter = (request.args.get("ehcp") or "").strip()
        vulnerable_filter = (request.args.get("vulnerable") or "").strip()
        attendance_band = (request.args.get("attendance_band") or "").strip()
        search = (request.args.get("search") or "").strip()
        show_all = parse_bool_filter(request.args.get("show_all")) is True

        classes = active_classes_query().order_by(SchoolClass.name.asc()).all()
        query = (
            db.session.query(Pupil)
            .join(SchoolClass, Pupil.class_id == SchoolClass.id)
            .outerjoin(PupilProfile, PupilProfile.pupil_id == Pupil.id)
            .filter(SchoolClass.is_archived.is_(False), SchoolClass.is_archive.is_(False))
        )

        if not show_all:
            query = query.filter(
                or_(
                    Pupil.pupil_premium.is_(True),
                    Pupil.laps.is_(True),
                    Pupil.service_child.is_(True),
                )
            )

        if class_filter and class_filter != "all":
            try:
                class_id = int(class_filter)
                query = query.filter(Pupil.class_id == class_id)
            except ValueError:
                pass

        if year_group_filter and year_group_filter != "all":
            if year_group_filter.lower() == "n":
                query = query.filter(or_(PupilProfile.year_group == 0, SchoolClass.year_group == 0))
            elif year_group_filter.lower() == "r":
                query = query.filter(or_(PupilProfile.year_group == -1, SchoolClass.year_group == -1))
            else:
                try:
                    yg = int(year_group_filter)
                    query = query.filter(or_(PupilProfile.year_group == yg, and_(PupilProfile.year_group.is_(None), SchoolClass.year_group == yg)))
                except ValueError:
                    pass

        for value, field in (
            (pp_filter, Pupil.pupil_premium),
            (laps_filter, Pupil.laps),
            (service_filter, Pupil.service_child),
            (send_filter, PupilProfile.send),
            (ehcp_filter, PupilProfile.ehcp),
            (vulnerable_filter, PupilProfile.vulnerable),
        ):
            parsed = parse_bool_filter(value)
            if parsed is not None:
                query = query.filter(field.is_(parsed))

        if search:
            query = query.filter(Pupil.name.ilike(f"%{search}%"))

        if attendance_band:
            if attendance_band == "lt90":
                query = query.filter(PupilProfile.attendance_spring1.isnot(None), PupilProfile.attendance_spring1 < 90)
            elif attendance_band == "90to95":
                query = query.filter(PupilProfile.attendance_spring1 >= 90, PupilProfile.attendance_spring1 <= 95)
            elif attendance_band == "gt95":
                query = query.filter(PupilProfile.attendance_spring1 > 95)

        pupils = query.order_by(SchoolClass.name.asc(), Pupil.name.asc()).all()
        pupil_ids = [p.id for p in pupils]
        profiles = {pr.pupil_id: pr for pr in PupilProfile.query.filter(PupilProfile.pupil_id.in_(pupil_ids)).all()} if pupil_ids else {}
        outcomes = current_outcomes_for_pupils(pupil_ids, year_id)

        return render_template(
            "admin_pupils_overview.html",
            pupils=pupils,
            profiles=profiles,
            outcomes=outcomes,
            years=years,
            sel_year_id=year_id,
            classes=classes,
            band_class_from_text=band_class_from_text,
            filters={
                "year_group": year_group_filter,
                "class": class_filter,
                "pp": pp_filter,
                "laps": laps_filter,
                "service": service_filter,
                "send": send_filter,
                "ehcp": ehcp_filter,
                "vulnerable": vulnerable_filter,
                "attendance_band": attendance_band,
                "search": search,
                "show_all": show_all,
            },
        )

    @app.route('/api/pupil_profile/update', methods=['POST'])
    @login_required
    def api_pupil_profile_update():
        if not is_admin_user():
            abort(403)

        data = request.get_json(silent=True) or {}
        pupil_id = int(data.get('pupil_id', 0))
        field = (data.get('field') or '').strip()
        value = data.get('value')

        pupil = Pupil.query.get_or_404(pupil_id)

        profile_bool_fields = {'lac_pla', 'send', 'ehcp', 'vulnerable', 'eyfs_gld'}
        profile_int_fields = {'year_group', 'y1_phonics', 'y2_phonics_retake'}
        profile_float_fields = {'attendance_spring1'}
        profile_text_fields = {'y2_reading', 'y2_writing', 'y2_maths', 'enrichment', 'interventions_note'}
        pupil_bool_fields = {'pupil_premium', 'service_child', 'laps'}

        if field in pupil_bool_fields:
            setattr(pupil, field, bool(value))
            pupil.updated_at = datetime.utcnow()
            db.session.commit()
            return jsonify({'ok': True})

        allowed_profile_fields = profile_bool_fields | profile_int_fields | profile_float_fields | profile_text_fields
        if field not in allowed_profile_fields:
            abort(400)

        profile = get_or_create_pupil_profile(pupil.id)

        if field in profile_bool_fields:
            if value in (None, '') and field == 'eyfs_gld':
                casted = None
            else:
                casted = bool(value)
        elif field in profile_int_fields:
            if value in (None, ''):
                casted = None
            else:
                try:
                    casted = int(str(value).strip())
                except ValueError:
                    return jsonify({'ok': False, 'error': 'Value must be a whole number'}), 400
        elif field in profile_float_fields:
            if value in (None, ''):
                casted = None
            else:
                try:
                    casted = float(str(value).strip())
                except ValueError:
                    return jsonify({'ok': False, 'error': 'Value must be numeric'}), 400
        else:
            casted = str(value or '').strip() or None

        setattr(profile, field, casted)
        db.session.commit()
        return jsonify({'ok': True})

    @app.route("/admin/pp_no_intervention")
    @login_required
    def admin_pp_no_intervention():
        if not getattr(current_user, "is_admin", False):
            flash("Admins only.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        try:
            year_id = int(request.args.get("year") or 0)
        except ValueError:
            year_id = 0
        try:
            class_id = int(request.args.get("class") or 0)
        except ValueError:
            class_id = 0
        term = (request.args.get("term") or "").strip()

        year = AcademicYear.query.get(year_id) if year_id else get_current_year()
        if not year:
            flash("No academic year found.", "error")
            return redirect(url_for("admin_overview"))

        pupil_q = Pupil.query.filter(Pupil.pupil_premium.is_(True))
        classes = active_classes_query().order_by(SchoolClass.name.asc()).all()
        if class_id:
            pupil_q = pupil_q.filter(Pupil.class_id == class_id)
        else:
            pupil_q = pupil_q.join(SchoolClass).filter(
                SchoolClass.is_archived.is_(False),
                SchoolClass.is_archive.is_(False),
            )
        pupils = pupil_q.order_by(Pupil.name.asc()).all()
        pupil_ids = [p.id for p in pupils]

        active_q = Intervention.query.filter(
            Intervention.academic_year_id == year.id,
            Intervention.status == "active"
        )
        if term in TERMS:
            active_q = active_q.filter(Intervention.term == term)
        active_ids = {pid for (pid,) in active_q.with_entities(Intervention.pupil_id).distinct().all()}

        rows = [p for p in pupils if p.id not in active_ids]

        back_params = {"year": year.id}
        if term in TERMS:
            back_params["term"] = term
        if class_id:
            back_params["class"] = class_id

        return render_template(
            "admin_pp_no_intervention.html",
            rows=rows,
            year=year,
            sel_term=term if term in TERMS else "",
            sel_class=class_id,
            classes=classes,
            back_url=url_for("admin_overview", **back_params),
        )

    @app.route("/admin/archive")
    @login_required
    def admin_archive():
        if not getattr(current_user, "is_admin", False):
            flash("Admins only.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        classes = SchoolClass.query.filter((SchoolClass.is_archived.is_(True)) | (SchoolClass.is_archive.is_(True))).order_by(SchoolClass.name.asc()).all()
        archived_class_ids = [c.id for c in classes]
        pupils = []
        if archived_class_ids:
            pupils = (Pupil.query
                      .filter(Pupil.class_id.in_(archived_class_ids))
                      .order_by(Pupil.name.asc())
                      .all())

        return render_template("admin_archive.html", classes=classes, pupils=pupils)

    @app.route("/admin/users")
    @login_required
    def admin_users():
        if not is_admin_user():
            flash("Admins only.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        users = Teacher.query.order_by(Teacher.is_admin.desc(), Teacher.username.asc()).all()
        return render_template("admin_users.html", users=users)

    @app.route("/admin/users/new", methods=["GET", "POST"])
    @login_required
    def admin_user_new():
        if not is_admin_user():
            flash("Admins only.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        form = AdminUserCreateForm()
        form.class_ids.choices = class_choices()
        if request.method == "GET":
            form.is_active.data = True

        if form.validate_on_submit():
            teacher = Teacher(
                username=form.username.data.strip(),
                is_admin=bool(form.is_admin.data),
                is_active=bool(form.is_active.data),
            )
            teacher.set_password(form.password.data)
            db.session.add(teacher)
            db.session.flush()

            selected_classes = SchoolClass.query.filter(SchoolClass.id.in_(form.class_ids.data or [])).all()
            for klass in selected_classes:
                db.session.add(TeacherClass(teacher_id=teacher.id, class_id=klass.id))
            if selected_classes and not teacher.class_id:
                teacher.class_id = selected_classes[0].id

            db.session.commit()
            flash("User created.", "success")
            return redirect(url_for("admin_users"))

        return render_template("admin_user_form.html", form=form, title="Create user", user=None)

    @app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
    @login_required
    def admin_user_edit(user_id):
        if not is_admin_user():
            flash("Admins only.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        user = Teacher.query.get_or_404(user_id)
        form = AdminUserEditForm(user_id=user.id)
        form.class_ids.choices = class_choices()

        if request.method == "GET":
            form.username.data = user.username
            form.is_admin.data = bool(user.is_admin)
            form.is_active.data = bool(getattr(user, "is_active", True))
            form.class_ids.data = [c.id for c in user.classes]

        if form.validate_on_submit():
            becoming_disabled = not bool(form.is_active.data)
            if user.id == current_user.id and becoming_disabled:
                flash("You cannot disable your own account.", "error")
                return redirect(url_for("admin_user_edit", user_id=user.id))

            admin_count = Teacher.query.filter_by(is_admin=True).count()
            if user.is_admin and not form.is_admin.data and admin_count <= 1:
                flash("Cannot remove admin rights from the last admin account.", "error")
                return redirect(url_for("admin_user_edit", user_id=user.id))

            user.username = form.username.data.strip()
            user.is_admin = bool(form.is_admin.data)
            user.is_active = bool(form.is_active.data)

            TeacherClass.query.filter_by(teacher_id=user.id).delete()
            selected_classes = SchoolClass.query.filter(SchoolClass.id.in_(form.class_ids.data or [])).all()
            for klass in selected_classes:
                db.session.add(TeacherClass(teacher_id=user.id, class_id=klass.id))

            selected_ids = [c.id for c in selected_classes]
            if selected_ids:
                if user.class_id not in selected_ids:
                    user.class_id = selected_ids[0]

            db.session.commit()
            flash("User updated.", "success")
            return redirect(url_for("admin_users"))

        return render_template("admin_user_form.html", form=form, title=f"Edit user: {user.username}", user=user)

    @app.route("/admin/users/<int:user_id>/reset_password", methods=["GET", "POST"])
    @login_required
    def admin_user_reset_password(user_id):
        if not is_admin_user():
            flash("Admins only.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        user = Teacher.query.get_or_404(user_id)
        form = AdminResetPasswordForm()
        if form.validate_on_submit():
            user.set_password(form.password.data)
            db.session.commit()
            flash("Password reset.", "success")
            return redirect(url_for("admin_users"))

        return render_template("admin_reset_password.html", form=form, user=user)

    @app.post("/admin/users/<int:user_id>/toggle_active")
    @login_required
    def admin_user_toggle_active(user_id):
        if not is_admin_user():
            flash("Admins only.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        user = Teacher.query.get_or_404(user_id)
        if user.id == current_user.id and getattr(user, "is_active", True):
            flash("You cannot disable your own account.", "error")
            return redirect(url_for("admin_users"))

        next_active = not bool(getattr(user, "is_active", True))
        if user.is_admin and not next_active:
            admin_active_count = Teacher.query.filter_by(is_admin=True, is_active=True).count()
            if admin_active_count <= 1:
                flash("Cannot disable the last active admin account.", "error")
                return redirect(url_for("admin_users"))

        user.is_active = next_active
        db.session.commit()
        flash("User enabled." if next_active else "User disabled.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/classes", methods=["GET", "POST"])
    @login_required
    def admin_classes():
        if not is_admin_user():
            flash("Admins only.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        if request.method == "POST":
            try:
                teacher_id = int(request.form.get("teacher_id", 0))
                class_id = int(request.form.get("class_id", 0))
            except ValueError:
                flash("Invalid teacher/class selection.", "error")
                return redirect(url_for("admin_classes"))

            teacher = Teacher.query.get(teacher_id)
            klass = SchoolClass.query.get(class_id)
            if not teacher or teacher.is_admin:
                flash("Please select a valid non-admin teacher.", "error")
                return redirect(url_for("admin_classes"))
            if not klass:
                flash("Please select a valid class.", "error")
                return redirect(url_for("admin_classes"))

            teacher.class_id = klass.id
            exists = TeacherClass.query.filter_by(teacher_id=teacher.id, class_id=klass.id).first()
            if not exists:
                db.session.add(TeacherClass(teacher_id=teacher.id, class_id=klass.id))
            db.session.commit()
            flash(f"Assigned {teacher.username} to {klass.name}.", "success")
            return redirect(url_for("admin_classes"))

        classes = SchoolClass.query.order_by(SchoolClass.year_group.asc(), SchoolClass.name.asc()).all()
        teachers = (Teacher.query
                    .filter_by(is_admin=False)
                    .order_by(Teacher.username.asc())
                    .all())
        return render_template("admin_classes.html", classes=classes, teachers=teachers)

    @app.route("/admin/promote", methods=["GET", "POST"])
    @login_required
    def admin_promote():
        if not is_admin_user():
            flash("Admins only.", "error")
            return redirect(url_for("dashboard", subject="maths"))

        classes = active_classes_query().order_by(SchoolClass.year_group.asc(), SchoolClass.name.asc()).all()
        current_year = get_current_year()
        promote_map = {1: 2, 2: 3, 3: 4, 4: 5, 5: 6}
        classes_by_year = {c.year_group: c for c in classes if c.year_group is not None}

        missing_year_groups = [yg for yg in range(1, 7) if yg not in classes_by_year]

        source_label = current_year.label if current_year else "2025/26"
        next_year_label = next_year_label_from(source_label)

        preview_rows = []
        for klass in classes:
            pupil_count = Pupil.query.filter_by(class_id=klass.id).count()
            next_label = "Unchanged"
            next_class_id = None
            if klass.year_group == 6:
                next_label = "Archive"
            elif klass.year_group in promote_map:
                target_year = promote_map[klass.year_group]
                target_class = classes_by_year.get(target_year)
                if target_class:
                    next_label = target_class.name
                    next_class_id = target_class.id
                else:
                    next_label = f"Year {target_year} class not found"
            preview_rows.append({
                "klass": klass,
                "pupil_count": pupil_count,
                "next_label": next_label,
                "next_class_id": next_class_id,
            })

        if request.method == "POST":
            if request.form.get("confirm_promote") != "1":
                flash("Please confirm promotion before continuing.", "error")
                return redirect(url_for("admin_promote"))

            if missing_year_groups:
                flash(
                    "Promotion aborted. Missing class year_group(s): " + ", ".join(str(y) for y in missing_year_groups),
                    "error",
                )
                return redirect(url_for("admin_promote"))

            next_year = AcademicYear.query.filter_by(label=next_year_label).first()
            if not next_year:
                next_year = AcademicYear(label=next_year_label, is_current=True)
                db.session.add(next_year)
                db.session.flush()

            AcademicYear.query.update({AcademicYear.is_current: False})
            next_year.is_current = True

            archive_class = get_or_create_archive_class()

            moved_total = 0
            archived_total = 0

            def add_history_row(pupil_id, class_id, academic_year_id):
                exists = PupilClassHistory.query.filter_by(
                    pupil_id=pupil_id,
                    class_id=class_id,
                    academic_year_id=academic_year_id,
                ).first()
                if not exists:
                    db.session.add(PupilClassHistory(
                        pupil_id=pupil_id,
                        class_id=class_id,
                        academic_year_id=academic_year_id,
                    ))

            all_pupils = Pupil.query.all()
            for pupil in all_pupils:
                klass = SchoolClass.query.get(pupil.class_id)
                if not klass or klass.year_group is None:
                    continue

                if current_year:
                    add_history_row(pupil.id, klass.id, current_year.id)

                if klass.year_group == 6:
                    pupil.class_id = archive_class.id
                    add_history_row(pupil.id, archive_class.id, next_year.id)
                    archived_total += 1
                elif klass.year_group in promote_map:
                    target_class = classes_by_year.get(klass.year_group + 1)
                    if not target_class:
                        db.session.rollback()
                        flash(
                            f"Promotion aborted. Missing destination class for Year {klass.year_group + 1}.",
                            "error",
                        )
                        return redirect(url_for("admin_promote"))
                    pupil.class_id = target_class.id
                    add_history_row(pupil.id, target_class.id, next_year.id)
                    moved_total += 1

            db.session.commit()
            flash(
                f"Promotion complete into {next_year_label}. Moved {moved_total} pupils and archived {archived_total} Year 6 pupils.",
                "success",
            )
            return redirect(url_for("admin_overview"))

        return render_template(
            "admin_promote.html",
            preview_rows=preview_rows,
            current_year=current_year,
            next_year_label=next_year_label,
            missing_year_groups=missing_year_groups,
        )



    # ---- Critical: return the Flask app object
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
