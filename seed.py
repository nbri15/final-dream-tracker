# seed.py
from app import create_app
from models import db, SchoolClass, Teacher, AcademicYear


def seed():
    app = create_app()
    with app.app_context():
        db.drop_all()
        db.create_all()

        # Create Academic Year(s)
        y = AcademicYear(label="2025/26", is_current=True)
        db.session.add(y)

        # Create 6 active classes: Year 1 .. Year 6
        classes = []
        for i in range(1, 7):
            classes.append(SchoolClass(name=f"Year {i}", year_group=i, is_archived=False, is_archive=False))
        archive = SchoolClass(name="Archive", year_group=None, is_archived=True, is_archive=True)
        db.session.add_all(classes + [archive])
        db.session.flush()  # so classes[i].id exists

        # Teachers (each locked to a class)
        teachers = []
        for i in range(1, 7):
            t = Teacher(username=f"teacher{i}", class_id=classes[i - 1].id, is_admin=False)
            t.set_password(f"password{i}")
            teachers.append(t)

        # Admin (no class)
        admin = Teacher(username="admin", is_admin=True)
        admin.set_password("admin 123!")

        db.session.add_all(teachers + [admin])
        db.session.commit()

        print("Seeded: 1 academic year, 6 active classes (Year 1..6), Archive class, 6 teachers, 1 admin (admin/'admin 123!').")


if __name__ == "__main__":
    seed()
