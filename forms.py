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


class DashboardTermConfigForm(FlaskForm):
    academic_year = HiddenField(validators=[DataRequired()])
    class_id = HiddenField(validators=[Optional()])
    subject = HiddenField(validators=[Optional()])
    mode = HiddenField(validators=[Optional()])
    return_url = HiddenField(validators=[Optional()])
    term = SelectField(
        "Term",
        choices=[("Autumn", "Autumn"), ("Spring", "Spring"), ("Summer", "Summer")],
        validators=[DataRequired()],
    )
    arithmetic_max = FloatField("Arithmetic max", validators=[DataRequired(), NumberRange(min=0.1)])
    reasoning_max = FloatField("Reasoning max", validators=[DataRequired(), NumberRange(min=0.1)])
    reading_p1_max = FloatField("Reading paper 1", validators=[DataRequired(), NumberRange(min=0.1)])
    reading_p2_max = FloatField("Reading paper 2", validators=[DataRequired(), NumberRange(min=0.1)])
    spelling_max = FloatField("Spelling", validators=[DataRequired(), NumberRange(min=0.1)])
    grammar_max = FloatField("Grammar", validators=[DataRequired(), NumberRange(min=0.1)])
    pass_percentage = FloatField("Pass %", validators=[DataRequired(), NumberRange(min=0, max=100)])
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

class CSVUploadResultsForm(FlaskForm):
    csv_file = FileField("Class CSV (.csv)", validators=[DataRequired()])
    academic_year = SelectField("Academic year", coerce=int, validators=[DataRequired()])
    term = SelectField(
        "Term",
        choices=[("Autumn", "Autumn"), ("Spring", "Spring"), ("Summer", "Summer")],
        validators=[DataRequired()],
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
