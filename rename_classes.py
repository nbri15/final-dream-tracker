from app import create_app
from models import db, SchoolClass

app = create_app()

with app.app_context():
    mapping = {
        1: "Year 1",
        2: "Year 2",
        3: "Year 3",
        4: "Year 4",
        5: "Year 5",
        6: "Year 6",
    }

    for c in SchoolClass.query.all():
        if c.id in mapping:
            c.name = mapping[c.id]

    db.session.commit()
    print("Classes renamed")
