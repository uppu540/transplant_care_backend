# TransplantCare API

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

Docs: http://localhost:8000/docs

Configure `MONGODB_URI`, `MONGODB_DATABASE`, and a random `JWT_SECRET` in `.env`
before starting the API. See `.env.example` for the supported settings.

For an existing Supabase deployment, set `SUPABASE_DB_URI`, `SUPABASE_URL`, and
`SUPABASE_SERVICE_ROLE_KEY` locally, then run
`python scripts/migrate_supabase_to_mongo.py` for a read-only count preview.
Review the counts and run with `--apply` to import rows and private lab-report
files. The import is repeatable; use `--skip-files` only if report files are
being migrated separately.

```bash
pytest
```
"# transplant_care_backend" 
