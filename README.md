# Dream Tracker

## Database migrations (Flask-Migrate / Alembic)
Use these commands to manage schema changes:

```bash
flask db init            # first time only
flask db migrate -m "..."
flask db upgrade
```
