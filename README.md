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

```bash
pytest
```
"# transplant_care_backend" 
