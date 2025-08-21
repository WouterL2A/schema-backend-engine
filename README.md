Here you go — two repo-ready Markdown files you can drop in.

---

# File: `README.md`

````markdown
# Schema Engines (Backend + Frontend)

Turn a Draft-07 **schema-of-entities** into a running **REST API** and a **dynamic admin UI** — without hand-coding models, routes, or forms.

- **schema-backend-engine**: parses your Draft-07 schema → generates an internal **Meta** model → builds SQLAlchemy models/tables → serves **CRUD** via FastAPI.
- **schema-frontend-engine**: reads the backend **Meta** at runtime → renders **list/view/create/edit** pages for every entity, including searchable FK fields and FK **labels** in tables.

---

## Quick Start

### Prerequisites
- **Python** 3.12+ (3.13 ok), **pip**
- **Node.js** 18+ and **npm** (or pnpm)
- A DB (SQLite for dev, or Postgres/MySQL)
- OS: Windows / macOS / Linux

### 1) Author your Draft-07 schema
Place it at `schema/schema_v3.json`. Example structure:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://example.com/schema_definitions/schema_v3.json",
  "title": "User Management Model",
  "type": "object",
  "properties": {
    "roles": { "$ref": "#/definitions/roles" },
    "users": { "$ref": "#/definitions/users" },
    "user_roles": { "$ref": "#/definitions/user_roles" },
    "sessions": { "$ref": "#/definitions/sessions" }
  },
  "definitions": {
    "...": {}
  }
}
````

Supported vendor extensions in properties:

* `x-primaryKey` (entity level, array of column names)
* `x-unique: true`
* `x-refTable`, `x-refColumn`, `x-relationshipName`
* Or use `$ref: "#/definitions/<table>"` to imply FK to `<table>.id`

### 2) Convert Draft-07 → Meta

```bash
# macOS/Linux
python3 conversion/schema_converter.py schema/schema_v3.json -o schema/schema.meta.json

# Windows PowerShell
python .\conversion\schema_converter.py .\schema\schema_v3.json -o .\schema\schema.meta.json
```

You’ll see verbose messages listing entities, columns, PK/FK, defaults.

> The Meta is validated by `schema_definitions/modelSchema.json` (expanded to allow real data types like BOOLEAN, DATE, DECIMAL, JSON, etc.).

### 3) Configure the backend

Create `.env` (or set env vars) for the backend:

```dotenv
DATABASE_URL=sqlite:///./dev.db
DIALECT=sqlite          # sqlite | postgresql | mysql
LOG_LEVEL=INFO
JWT_SECRET=dev-secret   # change in prod
```

Install backend deps and run the API:

```bash
pip install -r requirements.txt
uvicorn engine.main:app --reload --log-level info
```

Open `http://127.0.0.1:8000/docs`.

### 4) Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Set `VITE_API_URL` in `frontend/.env` if needed:

```env
VITE_API_URL=http://127.0.0.1:8000
```

---

## Repository Layout

```
conversion/
  schema_converter.py      # Draft-07 → Meta (CLI, verbose logs)
engine/
  main.py                  # FastAPI bootstrap
  routes.py                # dynamic CRUD routers
  db.py                    # SQLAlchemy engine/session
  ddl_builder.py           # builds SA models/tables from Meta
  type_mapping.py          # dialect-aware type mapper (NEW)
  meta_models.py           # Pydantic models for Meta
schema/
  schema_v3.json           # your authoring schema
  schema.meta.json         # generated Meta
schema_definitions/
  modelSchema.json         # Meta JSON Schema (EXPANDED)
frontend/
  src/ ...                 # dynamic UI driven by /meta
alembic/
  env.py, versions/ ...    # migrations (optional)
docs/
  requirements.md          # full product requirements
```

---

## Core Concepts

* **Draft-07 schema-of-entities**
  A convenient authoring format using `definitions`, `required`, `type`, `format`, `$ref`, `allOf`, and a few `x-*` hints.

* **Meta model (stable contract)**
  Small JSON describing tables/columns/PKs/FKs/types. Consumed by backend and frontend. Validated by `schema_definitions/modelSchema.json`.

* **Type support (production-ready)**
  `UUID | VARCHAR(len) | TEXT | INTEGER | BIGINT | DECIMAL(precision,scale) | FLOAT | BOOLEAN | DATE | TIMESTAMP | JSON | BLOB`

---

## Common Commands

**Regenerate Meta**

```bash
python conversion/schema_converter.py schema/schema_v3.json -o schema/schema.meta.json --quiet
```

**Run API**

```bash
uvicorn engine.main:app --log-level info
```

**Create DB tables**

```python
# within app startup, done automatically if configured
# from engine.ddl_builder import create_all_from_meta
# create_all_from_meta(...)
```

**Run frontend**

```bash
cd frontend && npm run dev
```

---

## Troubleshooting

* **Schema validation failed**: ensure `schema_definitions/modelSchema.json` is the expanded version in this repo. Re-run the converter.
* **Unknown data type**: check `engine/type_mapping.py` supports it for your dialect.
* **FK label not shown in UI**: ensure the backend supports `?include=<relation>` or the frontend has a label resolver for that FK’s target entity.
* **`process is not defined` in frontend**: use `import.meta.env.VITE_*` instead of `process.env.*` in Vite apps.

---

## License

TBD

## Contributing

PRs welcome. Please run formatter, linter, and unit tests. Add/update acceptance tests when changing Meta or type mappings.

````

---

# File: `docs/requirements.md`
```markdown
# Requirements — Schema Backend & Frontend Engines

## 1. Purpose
Automate CRUD backends and admin UIs from a single **Draft-07 schema-of-entities**. Eliminate repetitive model/route/form wiring while keeping production-grade correctness (types, FKs, RBAC, validation).

---

## 2. Architecture Overview

**Pipeline**

1. **Draft-07** (authoring)
2. **Converter** → **Meta JSON** (stable contract)
3. **Backend** builds models/tables and serves CRUD
4. **Frontend** consumes `/meta` to render UI

**Shared contract**: `schema_definitions/modelSchema.json` (expanded to real types).

---

## 3. Inputs & Conventions

### Draft-07 (authoring)
- Entities under `definitions.<entity>`.
- Fields in `properties`.
- `required` drives NOT NULL.
- Type mapping via `type` + `format` (e.g., `string/uuid`, `string/date-time`).
- FK inference:
  - Explicit: `x-refTable`, `x-refColumn`, optional `x-relationshipName`
  - Implicit: `$ref: "#/definitions/<table>"` → FK to `<table>.id`
- Primary key: `x-primaryKey` (array). If absent and field `id` exists, default PK is `["id"]`.
- Optional hints: `x-unique`, `default`, `maxLength`, `enum`, `allOf`.

### Meta (generator contract)
Validated by `schema_definitions/modelSchema.json`.

```json
{
  "$schema": "schema_definitions/modelSchema.json",
  "tables": [
    {
      "tableName": "users",
      "columns": [
        { "columnName": "id", "dataType": "UUID" },
        { "columnName": "email", "dataType": "VARCHAR", "length": 255, "isUnique": true },
        { "columnName": "created_at", "dataType": "TIMESTAMP", "defaultValue": "now" }
      ],
      "primaryKey": ["id"],
      "foreignKeys": [
        { "columnName": "role_id", "referencedTable": "roles", "referencedColumn": "id", "relationshipName": "role" }
      ]
    }
  ]
}
````

**Allowed data types**

```
UUID | VARCHAR | TEXT | INTEGER | BIGINT | DECIMAL | FLOAT | BOOLEAN | DATE | TIMESTAMP | JSON | BLOB
```

* `VARCHAR` requires `length`
* `DECIMAL` requires `precision` and `scale`

---

## 4. Converter (Draft-07 → Meta)

### Functional Requirements

* Resolve `$ref` and `allOf` (merge semantics).
* Map types/formats:

  * `string/uuid → UUID`
  * `string/date-time → TIMESTAMP`
  * `string/date → DATE`
  * `string` with `maxLength` → `VARCHAR(length)`; else default length 255
  * `integer → INTEGER`, `number → FLOAT`, `boolean → BOOLEAN`
  * Fallback: `TEXT`
* Derive FK from:

  * `x-refTable` + `x-refColumn`
  * `$ref:"#/definitions/<table>"` (defaults to `<table>.id`)
* Infer FK **column type** from target column schema.
* `required` → omit `isNullable` (NOT NULL); optional → `isNullable: true`.
* `x-unique` → `isUnique: true`
* Normalize default `now()/now` → `"now"`
* Entities included: all in root `properties` (ordering) **plus** remaining `definitions`.
* Console output (verbose):

  * entity, PK source (explicit/inferred), each column with flags (PK/NULL/UNIQUE/DEFAULT), and FK lines.

### CLI

```
python conversion/schema_converter.py <INPUT> -o <OUTPUT> [--schema-uri <URI>] [--quiet]
```

### Acceptance Criteria

* Converter output validates against `schema_definitions/modelSchema.json`.
* `$ref`-only FK columns are typed to the target PK’s type (e.g., UUID).
* Verbose run prints a summary for each entity.

---

## 5. Backend Engine

### Functional

* Load Meta JSON, validate against schema + Pydantic models.
* Build SQLAlchemy tables/models:

  * PKs, UNIQUE, NULL/NOT NULL, FKs.
  * Types mapped via `engine/type_mapping.py` (dialect-aware).
* Auto create DB schema on startup (dev) or via migration (prod).
* Dynamic CRUD routes for each entity:

  * `GET /meta`
  * `GET /entities`
  * `GET /{entity}/?limit&offset&sort=-field,field2&q=txt&filter=<json>`
  * `GET /{entity}/{id}`
  * `POST /{entity}`
  * `PATCH /{entity}/{id}`
  * `DELETE /{entity}/{id}`
  * `POST /{entity}/bulk`

**Filtering** (`filter` JSON):

```json
{
  "name": {"ilike": "%adm%"},
  "created_at": {"gte": "2024-01-01T00:00:00Z"},
  "role_id": {"eq": "..." }
}
```

**Relationships**

* `?include=user,role` → `selectinload` and embed as objects.
* Field selection: `?expand=user(name,email)`.

### Non-Functional

* P95 list latency ≤ 200ms @ 25 items (local DB).
* Avoid N+1 via `selectinload`.
* Structured logs (JSON): request id, route, status, latency.
* Health: `/healthz` (DB ping), `/readyz` (migrations applied).

### Security

* Auth endpoints:

  * `POST /auth/login` (email + password) → access/refresh tokens
  * `POST /auth/refresh`, `POST /auth/logout`
* RBAC:

  * Roles: Admin (all), Editor (CRUD except destructive), Viewer (read-only).
  * Policy matrix applied per entity/action.
* Passwords hashed (bcrypt), login rate-limit & lockout.

### Dialect Type Mapping (summary)

| Meta      | SQLite         | Postgres                  | MySQL                     |
| --------- | -------------- | ------------------------- | ------------------------- |
| UUID      | TEXT(36)       | `UUID(as_uuid=True)`      | `CHAR(36)`                |
| VARCHAR n | `String(n)`    | `String(n)`               | `String(n)`               |
| TEXT      | `Text`         | `Text`                    | `Text`                    |
| INTEGER   | `Integer`      | `Integer`                 | `Integer`                 |
| BIGINT    | `BigInteger`   | `BigInteger`              | `BigInteger`              |
| DECIMAL   | `Numeric(p,s)` | `Numeric(p,s)`            | `Numeric(p,s)`            |
| FLOAT     | `Float`        | `Float`                   | `Float`                   |
| BOOLEAN   | `Boolean`      | `Boolean`                 | `Boolean`                 |
| DATE      | `Date`         | `Date`                    | `Date`                    |
| TIMESTAMP | `DateTime(tz)` | `DateTime(timezone=True)` | `DateTime(timezone=True)` |
| JSON      | `JSON` (emul)  | `JSON`                    | `JSON` (5.7+)             |
| BLOB      | `LargeBinary`  | `LargeBinary`             | `LargeBinary`             |

### Acceptance

* Meta validates; tables build; CRUD endpoints live.
* FK includes/expands work without N+1.
* RBAC enforced correctly (403 on forbidden actions).

---

## 6. Frontend Engine

### Functional

* Fetch `/meta` on load → derive routes and screens per entity.
* **List**: columns labelized; FK columns display **labels** (not IDs); sorting; pagination; quick search; actions per row (Edit/Delete). **Add** button in header.
* **Form**:

  * Editors by type:

    * `UUID/INTEGER/BIGINT` → text/number (readOnly if PK)
    * `VARCHAR` → text with `maxLength`
    * `TEXT` → textarea
    * `DECIMAL/FLOAT` → numeric with step
    * `BOOLEAN` → switch
    * `DATE` → date picker
    * `TIMESTAMP` → date-time picker
    * `JSON` → code editor with validation
    * `BLOB` → file input (optional)
  * **FKs** → `ForeignKeySelect` (async, searchable):

    * Calls `GET /{refTable}?limit=20&q=<term>`
    * Stores ID; displays label from target entity (`name/title/first string column` heuristic or configured)
* **Detail**: read-only view; link to Edit.

### Tech

* React + TypeScript + Vite
* React Query + Axios
* React Router
* UI kit: MUI (or Tailwind+shadcn/ui — choose one consistently)

### API Assumptions

* Backend supports `limit/offset/sort/q`.
* `?include=` or separate label fetch for FK display.
* Errors: `{error:{code,message,details}}` → toast + field messages.

### UX Requirements

* Add button in list header.
* Edit/Delete buttons on each row.
* Searchable FK dropdowns.
* Responsive layout; keyboard accessible; proper ARIA labels.

### Acceptance

* Without custom wiring, `roles/users/user_roles/sessions` render end-to-end.
* FK labels render correctly in lists (e.g., `user_roles.user` shows `users.name`).
* Create/Edit/Delete flows work; validations displayed.

---

## 7. Ops & Tooling

* **Migrations**: Alembic scaffold; `alembic revision --autogenerate` after Meta change.
* **Config** via env: `DATABASE_URL`, `DIALECT`, `JWT_SECRET`, `LOG_LEVEL`.
* **Testing**:

  * Converter unit tests: type mapping, FK inference, required → NOT NULL
  * Backend API tests: CRUD happy path, validation errors, RBAC
  * Frontend tests: `ForeignKeySelect`, list sorting/search, form validation

---

## 8. Milestones

**M1 — Converter & Meta**

* Expanded `modelSchema.json`
* Converter emits real types; verbose logs
* Unit tests green

**M2 — Backend**

* Type mapper in place
* CRUD live; RBAC; health endpoints
* Alembic baseline

**M3 — Frontend**

* `/meta` → dynamic routes
* List/Form/View working; FK labels/selects
* Theming + accessibility baseline

**M4 — Quality**

* CI (lint/type/test)
* E2E for one entity flow
* Docs polished

---

## 9. Definition of Done

* [ ] Meta validates and is consistent with converter output
* [ ] DB schema builds from Meta, with PK/FK/constraints
* [ ] CRUD endpoints pass spec & tests
* [ ] Frontend renders entities dynamically; FK labels present
* [ ] Auth & RBAC enforced
* [ ] Logs/health endpoints operational
* [ ] Docs: this file + root README up to date

---

```

If you want, I can also add a `CONTRIBUTING.md`, a basic `requirements.txt`, and a `frontend/.env.example` to round out the repo.
```
