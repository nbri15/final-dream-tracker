import shutil
import uuid

import pytest

from app import create_app
from config import Config
from models import (
    Result,
    db,
    AcademicYear,
    Assessment,
    AssessmentQuestion,
    Pupil,
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


def _term_config_payload(year_id: int, class_id: int, overrides: dict | None = None):
    data = {
        "academic_year": str(year_id),
        "class_id": str(class_id),
        "subject": "maths",
        "mode": "table",
        "return_url": f"/dashboard/maths?mode=table&class={class_id}&year={year_id}&term=Autumn",
        "term": "Autumn",
        "arithmetic_max": "41",
        "reasoning_max": "39",
        "reading_p1_max": "44",
        "reading_p2_max": "43",
        "spelling_max": "28",
        "grammar_max": "32",
        "pass_percentage": "55",
        "submit": "Save settings",
    }
    if overrides:
        data.update(overrides)
    return data


def test_admin_inline_term_settings_persist_termconfig(client, test_app):
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

    payload = _term_config_payload(year_id, klass_id, {"arithmetic_max": "52.5"})
    resp = client.post("/term-config/save", data=payload, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with test_app.app_context():
        cfg = TermConfig.query.filter_by(class_id=klass_id, academic_year_id=year_id, term="Autumn").first()
        assert cfg is not None
        assert cfg.arith_max == pytest.approx(52.5)
        assert cfg.reason_max == pytest.approx(39.0)
        assert cfg.pass_percentage == pytest.approx(55.0)

    page = client.get(f"/dashboard/maths?mode=table&class={klass_id}&year={year_id}&term=Autumn")
    assert page.status_code == 200
    assert b'value="52.5"' in page.data


def test_teacher_inline_term_settings_persist_for_own_class(client, test_app):
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

    payload = _term_config_payload(year_id, klass_id, {"reasoning_max": "47.5", "pass_percentage": "62"})
    resp = client.post("/term-config/save", data=payload, follow_redirects=False)
    assert resp.status_code in (302, 303)

    with test_app.app_context():
        cfg = TermConfig.query.filter_by(class_id=klass_id, academic_year_id=year_id, term="Autumn").first()
        assert cfg is not None
        assert cfg.reason_max == pytest.approx(47.5)
        assert cfg.pass_percentage == pytest.approx(62.0)
        assert cfg.maths_wts_max == pytest.approx(62.0)
        assert cfg.reading_wts_max == pytest.approx(62.0)

    page = client.get(f"/dashboard/maths?mode=table&class={klass_id}&year={year_id}&term=Autumn")
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


def test_inline_term_settings_recalculate_existing_results(client, test_app):
    with test_app.app_context():
        year = AcademicYear.query.filter_by(is_current=True).first()
        klass = SchoolClass(name=f"RecalcClass-{uuid.uuid4().hex[:6]}", year_group=6)
        db.session.add(klass)
        db.session.commit()
        teacher = _make_user(f"recalc-{uuid.uuid4().hex[:6]}", klass.id, is_admin=False)
        pupil = Pupil(class_id=klass.id, name="Pupil A")
        db.session.add(pupil)
        db.session.flush()
        db.session.add(Result(
            pupil_id=pupil.id,
            academic_year_id=year.id,
            class_id_snapshot=klass.id,
            term="Autumn",
            subject="maths",
            arithmetic=20.0,
            reasoning=20.0,
        ))
        db.session.commit()
        teacher_username = teacher.username
        klass_id = klass.id
        year_id = year.id

    _login(client, teacher_username)
    resp = client.post(
        "/term-config/save",
        data=_term_config_payload(year_id, klass_id, {"arithmetic_max": "20", "reasoning_max": "20", "pass_percentage": "60"}),
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    with test_app.app_context():
        result = Result.query.filter_by(class_id_snapshot=klass_id, academic_year_id=year_id, term="Autumn", subject="maths").first()
        assert result is not None
        assert result.combined_pct == pytest.approx(100.0)
        assert result.summary == "Exceeding ARE"
