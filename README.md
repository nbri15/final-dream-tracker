# Dream Tracker

Dream Tracker is a Flask application for tracking class attainment, writing judgements, GAP analysis, Year 6 SATs, interventions, reports, and CSV-based result imports. The current repository has been trimmed so the checked-in files match the routes and features that are still active in the app.

## Project structure

- `app.py` - application factory, routes, view helpers, and lightweight schema compatibility checks for older local databases.
- `models.py` - SQLAlchemy models for pupils, results, GAP assessments, SATs, interventions, reports, and test papers.
- `forms.py` - WTForms definitions used by the active UI.
- `templates/` - active Jinja templates used by the app, including report templates and reusable partials.
- `migrations/versions/` - Alembic migrations that describe the tracked schema changes.
- `tests/` - automated regression coverage.
- `seed.py` - optional helper for creating a simple local demo dataset.

## Run locally

1. Create and activate a virtual environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Set the Flask application and apply migrations:

   ```bash
   export FLASK_APP=app.py
   flask db upgrade
   ```

4. Start the development server:

   ```bash
   python app.py
   ```

5. Open `http://127.0.0.1:8000` in your browser.

## Database migrations

Use Flask-Migrate / Alembic for schema changes:

```bash
flask db init            # first time only
flask db migrate -m "describe change"
flask db upgrade
```

## Notes on cleanup

- Legacy templates and scripts that were no longer referenced by active routes have been removed.
- Generated cache files and unused static assets have been removed from the repository.
- The remaining template set is the active surface used for dashboards, GAP, SATs, reports, interventions, and CSV import.
