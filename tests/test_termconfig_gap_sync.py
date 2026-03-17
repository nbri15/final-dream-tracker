import shutil
import uuid

import pytest

from app import create_app
from config import Config
from models import (
    db,
    AcademicYear,
    Assessment,
    AssessmentQuestion,
    SchoolClass,
    Teacher,
    TeacherClass,
    TermConfig,
)


@pytest.fixture()
def test_app(tmp_path):
    src_db = "dream.db"
    db_path = tmp_path / "test.db"
    shutil.copyfile(src_db, db_path)
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
    )
    yield app


@pytest.fixture()
def client(test_app):
    return test_app.test_client()


def _make_user(username: str, class_id: int, is_admin: bool = False):
    user = Teacher(username=username, class_id=class_id, is_admin=is_admin, is_active=True)
    user.set_password("password123")
    db.session.add(user)
    db.session.flush()
    db.session.add(TeacherClass(teacher_id=user.id, class_id=class_id))
    db.session.commit()
    return user


def _login(client, username: str):
    resp = client.post(
        "/login",
        data={"username": username, "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)


def _term_settings_payload(year_id: int, overrides: dict | None = None):
    data = {
        "academic_year": str(year_id),
        "autumn_arith_max": "41",
        "autumn_reason_max": "39",
        "autumn_reading_p1_max": "44",
        "autumn_reading_p2_max": "43",
        "autumn_spelling_max": "28",
        "autumn_grammar_max": "32",
        "spring_arith_max": "38",
        "spring_reason_max": "35",
        "spring_reading_p1_max": "40",
        "spring_reading_p2_max": "40",
        "spring_spelling_max": "40",
        "spring_grammar_max": "40",
        "summer_arith_max": "38",
        "summer_reason_max": "35",
        "summer_reading_p1_max": "40",
        "summer_reading_p2_max": "40",
        "summer_spelling_max": "40",
        "summer_grammar_max": "40",
        "autumn_maths_wts_max": "55",
        "autumn_maths_ot_max": "75",
        "autumn_reading_wts_max": "65",
        "autumn_reading_ot_max": "85",
        "autumn_spag_wts_max": "65",
        "autumn_spag_ot_max": "85",
        "spring_maths_wts_max": "55",
        "spring_maths_ot_max": "75",
        "spring_reading_wts_max": "65",
        "spring_reading_ot_max": "85",
        "spring_spag_wts_max": "65",
        "spring_spag_ot_max": "85",
        "summer_maths_wts_max": "55",
        "summer_maths_ot_max": "75",
        "summer_reading_wts_max": "65",
        "summer_reading_ot_max": "85",
        "summer_spag_wts_max": "65",
        "summer_spag_ot_max": "85",
        "submit": "Save settings",
    }
    if overrides:
        data.update(overrides)
    return data


def test_admin_term_settings_persists_paper_maxima(client, test_app):
    with test_app.app_context():
        year = AcademicYear.query.filter_by(is_current=True).first()
        klass = SchoolClass(name=f"Class-{uuid.uuid4().hex[:6]}", year_group=6)
        db.session.add(klass)
        db.session.commit()
        admin = _make_user(f"admin-{uuid.uuid4().hex[:6]}", klass.id, is_admin=True)
        admin_username = admin.username
        klass_id = klass.id
        year_id = year.id

    _login(client, admin_username)

    payload = _term_settings_payload(year_id, {"class_id": str(klass_id), "autumn_arith_max": "52.5"})
    resp = client.post("/settings/terms", data=payload, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with test_app.app_context():
        cfg = TermConfig.query.filter_by(class_id=klass_id, academic_year_id=year_id, term="Autumn").first()
        assert cfg is not None
        assert cfg.arith_max == pytest.approx(52.5)
        assert cfg.reason_max == pytest.approx(39.0)

    page = client.get(f"/settings/terms?class_id={klass_id}&year={year_id}")
    assert page.status_code == 200
    assert b'value="52.5"' in page.data


def test_teacher_term_settings_persists_for_own_class(client, test_app):
    with test_app.app_context():
        year = AcademicYear.query.filter_by(is_current=True).first()
        klass = SchoolClass(name=f"TeacherClass-{uuid.uuid4().hex[:6]}", year_group=5)
        db.session.add(klass)
        db.session.commit()
        teacher = _make_user(f"teacher-{uuid.uuid4().hex[:6]}", klass.id, is_admin=False)
        teacher_username = teacher.username
        klass_id = klass.id
        year_id = year.id

    _login(client, teacher_username)

    payload = _term_settings_payload(year_id, {"autumn_reason_max": "47.5"})
    resp = client.post("/settings/terms", data=payload, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with test_app.app_context():
        cfg = TermConfig.query.filter_by(class_id=klass_id, academic_year_id=year_id, term="Autumn").first()
        assert cfg is not None
        assert cfg.reason_max == pytest.approx(47.5)

    page = client.get(f"/settings/terms?year={year_id}")
    assert page.status_code == 200
    assert b'value="47.5"' in page.data


def test_gap_sync_keeps_question_structure_and_adjusts_last_only(client, test_app):
    with test_app.app_context():
        year = AcademicYear.query.filter_by(is_current=True).first()
        klass = SchoolClass(name=f"GapClass-{uuid.uuid4().hex[:6]}", year_group=6)
        db.session.add(klass)
        db.session.commit()
        teacher = _make_user(f"gapteacher-{uuid.uuid4().hex[:6]}", klass.id, is_admin=False)
        teacher_username = teacher.username

        assessment = Assessment(
            class_id=klass.id,
            academic_year_id=year.id,
            term="Autumn",
            subject="maths",
            paper="Arithmetic",
            title="GAP test",
        )
        db.session.add(assessment)
        db.session.flush()
        db.session.add_all([
            AssessmentQuestion(assessment_id=assessment.id, number=1, max_mark=2.0),
            AssessmentQuestion(assessment_id=assessment.id, number=2, max_mark=3.0),
            AssessmentQuestion(assessment_id=assessment.id, number=3, max_mark=4.0),
        ])
        db.session.commit()
        assessment_id = assessment.id
        klass_id = klass.id
        year_id = year.id

    _login(client, teacher_username)
    resp = client.post(f"/assessments/{assessment_id}/set-max-score", data={"max_score": "12"}, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with test_app.app_context():
        qs = AssessmentQuestion.query.filter_by(assessment_id=assessment_id).order_by(AssessmentQuestion.number.asc()).all()
        assert [q.number for q in qs] == [1, 2, 3]
        assert len(qs) == 3
        assert qs[0].max_mark == pytest.approx(2.0)
        assert qs[1].max_mark == pytest.approx(3.0)
        assert qs[2].max_mark == pytest.approx(7.0)

        cfg = TermConfig.query.filter_by(class_id=klass_id, academic_year_id=year_id, term="Autumn").first()
        assert cfg is not None
        assert cfg.arith_max == pytest.approx(12.0)
