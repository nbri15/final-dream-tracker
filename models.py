# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ----- Core domain -----

class SchoolClass(db.Model):
    __tablename__ = "classes"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    year_group = db.Column(db.Integer, nullable=True)  # e.g., 1..13 (Year 1..Year 13)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    is_archive = db.Column(db.Boolean, nullable=False, default=False)

    pupils = db.relationship("Pupil", backref="klass", cascade="all, delete-orphan")
    teachers = db.relationship("Teacher", backref="klass", foreign_keys="Teacher.class_id")
    teacher_links = db.relationship("TeacherClass", back_populates="school_class", cascade="all, delete-orphan")


class TeacherClass(db.Model):
    __tablename__ = "teacher_classes"
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"), primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    teacher = db.relationship("Teacher", back_populates="class_links")
    school_class = db.relationship("SchoolClass", back_populates="teacher_links")


class Teacher(UserMixin, db.Model):
    __tablename__ = "teachers"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"))
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    class_links = db.relationship("TeacherClass", back_populates="teacher", cascade="all, delete-orphan")
    classes = db.relationship("SchoolClass", secondary="teacher_classes", viewonly=True)

    def set_password(self, raw: str):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    @property
    def is_teacher(self):
        return True

    @property
    def primary_class(self):
        if self.class_id:
            return SchoolClass.query.get(self.class_id)
        return self.classes[0] if self.classes else None


class Pupil(db.Model):
    __tablename__ = "pupils"
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"), nullable=False)
    number = db.Column(db.Integer, nullable=True)  # optional display No.
    name = db.Column(db.String(120), nullable=False, index=True)
    gender = db.Column(db.String(1), nullable=True)  # 'M' or 'F'
    pupil_premium = db.Column(db.Boolean, default=False)
    laps = db.Column(db.Boolean, default=False)
    service_child = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    results = db.relationship("Result", backref="pupil", cascade="all, delete-orphan")
    writing_results = db.relationship("WritingResult", backref="pupil", cascade="all, delete-orphan")
    profile = db.relationship("PupilProfile", back_populates="pupil", uselist=False, cascade="all, delete-orphan")
    sats_scores = db.relationship("SatsScore", backref="pupil", cascade="all, delete-orphan")
    class_history = db.relationship("PupilClassHistory", backref="pupil", cascade="all, delete-orphan")

    __table_args__ = (
        # Uncomment if you want to enforce one register number per class:
        # db.UniqueConstraint('class_id', 'number', name='uq_pupil_number_per_class'),
    )


# ----- Academic years -----

class AcademicYear(db.Model):
    __tablename__ = "academic_years"
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(9), unique=True, nullable=False)  # "2025/26"
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    is_current = db.Column(db.Boolean, default=False, nullable=False)

    def __repr__(self):
        return f"<AcademicYear {self.label}{' (current)' if self.is_current else ''}>"


class PupilClassHistory(db.Model):
    __tablename__ = "pupil_class_history"
    id = db.Column(db.Integer, primary_key=True)
    pupil_id = db.Column(db.Integer, db.ForeignKey("pupils.id"), nullable=False, index=True)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"), nullable=False, index=True)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    klass = db.relationship("SchoolClass")
    academic_year = db.relationship("AcademicYear")

    __table_args__ = (
        db.UniqueConstraint("pupil_id", "class_id", "academic_year_id", name="uq_pupil_class_history"),
    )


class SatsHeader(db.Model):
    __tablename__ = "sats_headers"
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"), nullable=False)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False)
    key = db.Column(db.String(20), nullable=False)
    header = db.Column(db.String(120), nullable=True)
    group = db.Column(db.String(20), nullable=False, index=True)
    order = db.Column(db.Integer, nullable=False, default=0)

    klass = db.relationship("SchoolClass")
    academic_year = db.relationship("AcademicYear")

    __table_args__ = (
        db.UniqueConstraint("class_id", "academic_year_id", "key", name="uq_sats_header_unique"),
    )


class SatsScore(db.Model):
    __tablename__ = "sats_scores"
    id = db.Column(db.Integer, primary_key=True)
    pupil_id = db.Column(db.Integer, db.ForeignKey("pupils.id"), nullable=False)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False)
    key = db.Column(db.String(20), nullable=False, index=True)
    value = db.Column(db.Float, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    academic_year = db.relationship("AcademicYear")

    __table_args__ = (
        db.UniqueConstraint(
            "pupil_id", "academic_year_id", "key", name="uq_sats_score_unique"
        ),
    )


class Result(db.Model):
    __tablename__ = "results"
    id = db.Column(db.Integer, primary_key=True)

    pupil_id = db.Column(db.Integer, db.ForeignKey("pupils.id"), nullable=False)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False)
    academic_year = db.relationship("AcademicYear")

    class_id_snapshot = db.Column(db.Integer, db.ForeignKey("classes.id"), nullable=True)

    term = db.Column(db.String(10), nullable=False)  # "Autumn" | "Spring" | "Summer"
    subject = db.Column(db.String(20), nullable=False, default="maths", index=True)
    arithmetic = db.Column(db.Float, nullable=True)
    reasoning = db.Column(db.Float, nullable=True)
    reading_p1 = db.Column(db.Float, nullable=True)
    reading_p2 = db.Column(db.Float, nullable=True)
    spelling = db.Column(db.Float, nullable=True)
    grammar = db.Column(db.Float, nullable=True)
    combined_pct = db.Column(db.Float, nullable=True)  # auto-calculated
    summary = db.Column(db.String(30), nullable=True)  # auto-assigned band (text)
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('pupil_id', 'academic_year_id', 'term', 'subject', name='uq_pupil_year_term_subject'),
    )


class WritingResult(db.Model):
    __tablename__ = "writing_results"
    id = db.Column(db.Integer, primary_key=True)

    pupil_id = db.Column(db.Integer, db.ForeignKey("pupils.id"), nullable=False)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False)
    academic_year = db.relationship("AcademicYear")

    term = db.Column(db.String(10), nullable=False)  # "Autumn" | "Spring" | "Summer"
    band = db.Column(db.String(30), nullable=False)  # working_towards | working_at | exceeding
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('pupil_id', 'academic_year_id', 'term', name='uq_writing_pupil_year_term'),
    )


class PupilProfile(db.Model):
    __tablename__ = "pupil_profiles"

    id = db.Column(db.Integer, primary_key=True)
    pupil_id = db.Column(db.Integer, db.ForeignKey("pupils.id"), nullable=False, unique=True, index=True)
    year_group = db.Column(db.Integer, nullable=True)
    lac_pla = db.Column(db.Boolean, nullable=False, default=False)
    send = db.Column(db.Boolean, nullable=False, default=False)
    ehcp = db.Column(db.Boolean, nullable=False, default=False)
    vulnerable = db.Column(db.Boolean, nullable=False, default=False)
    attendance_spring1 = db.Column(db.Float, nullable=True)
    eyfs_gld = db.Column(db.Boolean, nullable=True)
    y1_phonics = db.Column(db.Integer, nullable=True)
    y2_phonics_retake = db.Column(db.Integer, nullable=True)
    y2_reading = db.Column(db.String(30), nullable=True)
    y2_writing = db.Column(db.String(30), nullable=True)
    y2_maths = db.Column(db.String(30), nullable=True)
    enrichment = db.Column(db.Text, nullable=True)
    interventions_note = db.Column(db.Text, nullable=True)

    pupil = db.relationship("Pupil", back_populates="profile")


class TermConfig(db.Model):
    __tablename__ = "term_configs"
    id = db.Column(db.Integer, primary_key=True)

    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"), nullable=False)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False)
    academic_year = db.relationship("AcademicYear")

    term = db.Column(db.String(10), nullable=False)  # "Autumn" | "Spring" | "Summer"
    arith_max = db.Column(db.Float, nullable=False, default=38.0)
    reason_max = db.Column(db.Float, nullable=False, default=35.0)
    reading_p1_max = db.Column(db.Float, nullable=True)
    reading_p2_max = db.Column(db.Float, nullable=True)
    spelling_max = db.Column(db.Float, nullable=True)
    grammar_max = db.Column(db.Float, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('class_id', 'academic_year_id', 'term', name='uq_class_year_term'),
    )


# ----- Interventions -----

class Intervention(db.Model):
    __tablename__ = "interventions"
    id = db.Column(db.Integer, primary_key=True)
    pupil_id = db.Column(db.Integer, db.ForeignKey("pupils.id"), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"), nullable=False)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False)
    term = db.Column(db.String(10), nullable=False)         # "Autumn" | "Spring" | "Summer"
    paper = db.Column(db.String(20), nullable=False)        # "Arithmetic" | "Reasoning"
    pct = db.Column(db.Float, nullable=True)                # pupil % on that paper
    status = db.Column(db.String(20), nullable=False, default="proposed")  # proposed/active/closed
    selected_by = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=True)
    support_plan = db.Column(db.Text, nullable=True)        # editable by admin
    teacher_note = db.Column(db.Text, nullable=True)
    teacher_updated_at = db.Column(db.DateTime, nullable=True)
    focus_areas = db.Column(db.Text, nullable=True)
    pre_result = db.Column(db.String(120), nullable=True)
    post_result = db.Column(db.String(120), nullable=True)
    review_due_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pupil = db.relationship("Pupil")
    klass = db.relationship("SchoolClass")
    year = db.relationship("AcademicYear")
    selector = db.relationship("Teacher")

    __table_args__ = (
        db.UniqueConstraint('pupil_id', 'academic_year_id', 'term', 'paper', name='uq_intervention_unique'),
    )

    @property
    def pre_score_value(self):
        try:
            return float(self.pre_result) if self.pre_result not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @property
    def post_score_value(self):
        try:
            return float(self.post_result) if self.post_result not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @property
    def impact(self):
        pre = self.pre_score_value
        post = self.post_score_value
        if pre is None or post is None:
            return None
        return round(post - pre, 2)

    @property
    def impact_pct(self):
        pre = self.pre_score_value
        post = self.post_score_value
        if pre is None or post is None:
            return None
        return round(((post - pre) / max(pre, 1.0)) * 100.0, 1)


class PupilReportNote(db.Model):
    __tablename__ = "pupil_report_notes"
    id = db.Column(db.Integer, primary_key=True)
    pupil_id = db.Column(db.Integer, db.ForeignKey("pupils.id"), nullable=False, index=True)
    year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False, index=True)
    term_id = db.Column(db.String(10), nullable=False, index=True)
    strengths_text = db.Column(db.Text, nullable=True)
    next_steps_text = db.Column(db.Text, nullable=True)
    updated_by = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    pupil = db.relationship("Pupil")
    year = db.relationship("AcademicYear")
    updated_by_teacher = db.relationship("Teacher")

    __table_args__ = (
        db.UniqueConstraint("pupil_id", "year_id", "term_id", name="uq_pupil_report_note"),
    )


# ----- GAP analysis -----

class Assessment(db.Model):
    __tablename__ = "assessments"
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"), nullable=False)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False)
    term = db.Column(db.String(10), nullable=False)  # Autumn/Spring/Summer
    subject = db.Column(db.String(20), nullable=False, default="maths", index=True)
    paper = db.Column(db.String(20), nullable=False, default="Arithmetic", index=True)
    title = db.Column(db.String(120), nullable=False)
    template_id = db.Column(db.Integer, db.ForeignKey("paper_templates.id"), nullable=True)
    template_version = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    klass = db.relationship("SchoolClass")
    academic_year = db.relationship("AcademicYear")

    questions = db.relationship("AssessmentQuestion", backref="assessment", cascade="all, delete-orphan")
    scores = db.relationship("PupilQuestionScore", backref="assessment", cascade="all, delete-orphan")


class AssessmentQuestion(db.Model):
    __tablename__ = "assessment_questions"
    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessments.id"), nullable=False)
    number = db.Column(db.Integer, nullable=False)  # Q1, Q2, ...
    max_mark = db.Column(db.Float, nullable=False, default=1.0)
    strand = db.Column(db.String(80), nullable=True)
    question_type = db.Column(db.String(120), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('assessment_id', 'number', name='uq_assess_qn'),
    )


class PupilQuestionScore(db.Model):
    __tablename__ = "pupil_question_scores"
    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessments.id"), nullable=False)
    pupil_id = db.Column(db.Integer, db.ForeignKey("pupils.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("assessment_questions.id"), nullable=False)
    mark = db.Column(db.Float, nullable=False, default=0.0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=True)

    pupil = db.relationship("Pupil")
    question = db.relationship("AssessmentQuestion")

    __table_args__ = (
        db.UniqueConstraint('pupil_id', 'question_id', name='uq_pupil_qn'),
    )


class PaperTemplate(db.Model):
    __tablename__ = "paper_templates"
    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(20), nullable=False, index=True)
    paper = db.Column(db.String(30), nullable=False, index=True)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False, index=True)
    year_group = db.Column(db.Integer, nullable=False, index=True)
    term = db.Column(db.String(10), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    academic_year = db.relationship("AcademicYear")
    questions = db.relationship("PaperTemplateQuestion", backref="template", cascade="all, delete-orphan")


class PaperTemplateQuestion(db.Model):
    __tablename__ = "paper_template_questions"
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("paper_templates.id"), nullable=False, index=True)
    number = db.Column(db.Integer, nullable=False)
    max_mark = db.Column(db.Float, nullable=False, default=1.0)
    question_type = db.Column(db.String(120), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    strand = db.Column(db.String(120), nullable=True)

    __table_args__ = (
        db.UniqueConstraint("template_id", "number", name="uq_template_question_number"),
    )
