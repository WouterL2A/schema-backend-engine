Awesome — here are the three files, ready to drop into your repo.

---

## `CONTRIBUTING.md`

```markdown
# Contributing

Thanks for helping improve the schema engines! This guide keeps contributions fast, predictable, and production-ready.

## Project Layout (at a glance)
```

conversion/                # Draft-07 → Meta converter (CLI)
engine/                    # FastAPI app + SQLAlchemy builders
frontend/                  # React/Vite admin UI driven by /meta
schema/                    # Your authoring schema + generated meta
schema\_definitions/        # Meta JSON Schema (expanded types)
tests/                     # Unit/integration tests

````

## Prerequisites
- **Python** 3.12+ (3.13 OK)
- **Node.js** 18+ (with npm or pnpm)
- A DB (SQLite for dev; Postgres/MySQL supported)
- Git

## First-time Setup
```bash
# Python
python -m venv .venv && source .venv/bin/activate   # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt

# Frontend
cd frontend
npm install
````

## Common Dev Commands

```bash
# Regenerate Meta from Draft-07
python conversion/schema_converter.py schema/schema_v3.json -o schema/schema.meta.json

# Run API
uvicorn engine.main:app --reload --log-level info

# Run Frontend
cd frontend && npm run dev
```

## Branching & Commits

* **Branches**: `feat/<short-name>`, `fix/<short-name>`, `chore/<short-name>`
* **Conventional Commits** (please):

  * `feat: add DECIMAL support`
  * `fix: correct FK inference for $ref`
  * `chore: bump FastAPI`

## Code Style & Quality

**Python**

* Formatter: `black`
* Import order: `isort`
* Lint: `ruff`
* Type checking: `pyright` (or `mypy`)

**Commands**

```bash
black .
isort .
ruff check .
```

**TypeScript/React**

* Lint: `eslint`
* Format: `prettier`

```bash
cd frontend
npm run lint
npm run format
```

## Testing

* **Converter**: unit tests for type mapping, `$ref`/`allOf` resolution, required→NOT NULL.
* **Backend**: pytest + httpx for CRUD flows; DB setup via SQLite temp DB.
* **Frontend**: unit tests for FK select + list sorting; e2e optional.

```bash
pytest -q
# or specific test
pytest tests/test_converter.py -q
```

## Pull Requests

* Keep PRs focused and small.
* Include before/after notes or screenshots for UI changes.
* Ensure:

  * [ ] Meta regenerates & validates
  * [ ] API starts cleanly
  * [ ] Lint + tests pass

## Versioning & Releases

* SemVer for published packages/tags.
* Document breaking changes in `CHANGELOG.md`.

## Security

* Never commit secrets. Use `.env` files (ignored).
* Report vulnerabilities privately.

## Where to Ask

* Open a **Discussion** for design questions.
* Open an **Issue** for bugs or actionable feature requests.

````

---

## `requirements.txt`
```txt
# --- Core API stack ---
fastapi>=0.110,<1.0
uvicorn[standard]>=0.23,<1.0

# --- Data & models ---
SQLAlchemy>=2.0,<3.0
alembic>=1.12,<2.0
pydantic>=2.5,<3.0

# --- Auth & security ---
passlib[bcrypt]>=1.7,<2.0
PyJWT>=2.8,<3.0

# --- Utilities ---
python-dotenv>=1.0,<2.0
orjson>=3.9,<4.0
email-validator>=2.0,<3.0

# --- Optional DB drivers (install only what you need) ---
psycopg[binary]>=3.1,<4.0     # PostgreSQL
PyMySQL>=1.1,<2.0             # MySQL/MariaDB
# sqlite - built into Python

# --- Dev / Testing (optional; move to dev-requirements.txt if you prefer) ---
pytest>=8.0,<9.0
httpx>=0.27,<0.28
ruff>=0.4,<0.6
black>=24.3,<25.0
isort>=5.13,<6.0
````

> Tip: if you prefer strict pinning, freeze to a `requirements-lock.txt` after a green CI run:
> `pip freeze | sed '/^-e /d' > requirements-lock.txt`

---

## `frontend/.env.example`

```env
# Base URL of the backend FastAPI API
VITE_API_URL=http://127.0.0.1:8000

# Toggle auth integration in the UI (guards, login, tokens)
VITE_AUTH_ENABLED=true

# Default page size for list views
VITE_PAGE_SIZE_DEFAULT=25

# Whether the backend supports ?include=/expand for FK labels
VITE_FEATURE_INCLUDE_EXPAND=true

# UI logging level: silent | error | warn | info | debug
VITE_LOG_LEVEL=info
```

If you want matching **backend** `.env.example` as well (DATABASE\_URL, DIALECT, JWT\_SECRET, etc.), say the word and I’ll add it.
