# forms.py
from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, SubmitField, BooleanField, SelectField, SelectMultipleField,
    IntegerField, FloatField, TextAreaField, FileField, HiddenField
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError
from sqlalchemy import func
from models import Teacher


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=2, max=80)])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log in")


class PupilForm(FlaskForm):
    number = IntegerField("No.", validators=[Optional()])
    name = StringField("Name", validators=[DataRequired(), Length(min=1, max=120)])
    gender = SelectField("Gender", choices=[("", "-"), ("F", "F"), ("M", "M")], validators=[Optional()])
    pupil_premium = BooleanField("Pupil Premium")
    laps = BooleanField("LAPS")
    service_child = BooleanField("Service child")
    submit = SubmitField("Save pupil")


class ResultForm(FlaskForm):
    term = SelectField("Term",
                       choices=[("Autumn", "Autumn"), ("Spring", "Spring"), ("Summer", "Summer")],
                       validators=[DataRequired()])
    academic_year = SelectField("Academic year", coerce=int, validators=[DataRequired()])
    arithmetic = FloatField("Arithmetic", validators=[Optional(), NumberRange(min=0)])
    reasoning = FloatField("Reasoning", validators=[Optional(), NumberRange(min=0)])
    note = TextAreaField("Notes", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("Save result")


class TermSettingsForm(FlaskForm):
    academic_year = SelectField("Academic year", coerce=int, validators=[DataRequired()])
    autumn_arith_max = FloatField("Autumn Arithmetic max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    autumn_reason_max = FloatField("Autumn Reasoning max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    autumn_reading_p1_max = FloatField("Autumn Reading Paper 1 max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    autumn_reading_p2_max = FloatField("Autumn Reading Paper 2 max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    autumn_spelling_max = FloatField("Autumn Spelling max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    autumn_grammar_max = FloatField("Autumn Grammar max", default=50, validators=[DataRequired(), NumberRange(min=1)])

    spring_arith_max = FloatField("Spring Arithmetic max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    spring_reason_max = FloatField("Spring Reasoning max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    spring_reading_p1_max = FloatField("Spring Reading Paper 1 max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    spring_reading_p2_max = FloatField("Spring Reading Paper 2 max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    spring_spelling_max = FloatField("Spring Spelling max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    spring_grammar_max = FloatField("Spring Grammar max", default=50, validators=[DataRequired(), NumberRange(min=1)])

    summer_arith_max = FloatField("Summer Arithmetic max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    summer_reason_max = FloatField("Summer Reasoning max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    summer_reading_p1_max = FloatField("Summer Reading Paper 1 max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    summer_reading_p2_max = FloatField("Summer Reading Paper 2 max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    summer_spelling_max = FloatField("Summer Spelling max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    summer_grammar_max = FloatField("Summer Grammar max", default=50, validators=[DataRequired(), NumberRange(min=1)])
    submit = SubmitField("Save settings")


# --- Admin: Years ---

class YearForm(FlaskForm):
    label = StringField("Label (e.g., 2025/26)", validators=[DataRequired(), Length(min=7, max=9)])
    start_date = StringField("Start date (optional, yyyy-mm-dd)", validators=[Optional(), Length(max=10)])
    end_date = StringField("End date (optional, yyyy-mm-dd)", validators=[Optional(), Length(max=10)])
    is_current = BooleanField("Set as current year")
    submit = SubmitField("Add year")


class SetCurrentYearForm(FlaskForm):
    year_id = HiddenField(validators=[DataRequired()])
    submit = SubmitField("Set current")


# --- CSV imports ---

class CSVUploadPupilsForm(FlaskForm):
    csv_file = FileField("Pupils CSV (.csv)", validators=[DataRequired()])
    # Teachers are constrained to their class in the view; admins can choose:
    class_id = SelectField("Class (admins)", coerce=int, validators=[Optional()])
    submit_preview = SubmitField("Upload & Preview")
    submit_confirm = SubmitField("Confirm Import")
    token = HiddenField()  # simple reconciliation token for preview/confirm


class CSVUploadResultsForm(FlaskForm):
    csv_file = FileField("Class CSV (.csv)", validators=[DataRequired()])
    academic_year = SelectField("Academic year", coerce=int, validators=[DataRequired()])
    term = SelectField(
        "Term",
        choices=[("Autumn", "Autumn"), ("Spring", "Spring"), ("Summer", "Summer")],
        validators=[DataRequired()],
    )
    subject = SelectField(
        "Subject",
        choices=[
            ("maths", "Maths"),
            ("reading", "Reading"),
            ("spag", "SPaG"),
            ("writing", "Writing"),
        ],
        validators=[Optional()],
        default="maths",
    )
    # Admin can choose class; teachers are restricted in the route
    class_id = SelectField("Class (admins)", coerce=int, validators=[Optional()])

    submit_preview = SubmitField("Upload & Preview")
    submit_confirm = SubmitField("Confirm Import")
    token = HiddenField()




# --- Class settings ---

class ClassSettingsForm(FlaskForm):
    YEAR_GROUP_CHOICES = [(i, f"Year {i}") for i in range(1, 14)]
    year_group = SelectField("Year group", coerce=int, choices=YEAR_GROUP_CHOICES)
    submit = SubmitField("Save class settings")


class AdminUserCreateForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=2, max=80)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    is_admin = BooleanField("Admin")
    is_active = BooleanField("Active", default=True)
    class_ids = SelectMultipleField("Assigned classes", coerce=int, validators=[Optional()])
    submit = SubmitField("Create user")

    def validate_username(self, field):
        existing = Teacher.query.filter(func.lower(Teacher.username) == field.data.strip().lower()).first()
        if existing:
            raise ValidationError("Username already exists.")


class AdminUserEditForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=2, max=80)])
    is_admin = BooleanField("Admin")
    is_active = BooleanField("Active", default=True)
    class_ids = SelectMultipleField("Assigned classes", coerce=int, validators=[Optional()])
    submit = SubmitField("Save")

    def __init__(self, user_id=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_id = user_id

    def validate_username(self, field):
        q = Teacher.query.filter(func.lower(Teacher.username) == field.data.strip().lower())
        if self.user_id:
            q = q.filter(Teacher.id != self.user_id)
        if q.first():
            raise ValidationError("Username already exists.")


class AdminResetPasswordForm(FlaskForm):
    password = PasswordField("New password", validators=[DataRequired(), Length(min=8, max=128)])
    submit = SubmitField("Reset password")
