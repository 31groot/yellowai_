PY := backend/.venv/bin/python
export PYTHONPATH := backend

.PHONY: setup db seed demo test run api ui build simulate

setup:            ## venv + deps + DB + seed
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install -q -r backend/requirements.txt
	cd frontend && npm install --no-audit --no-fund
	$(MAKE) db seed

db:               ## start local Postgres and create the databases (Debian/Ubuntu pg_ctlcluster)
	-pg_ctlcluster 16 main start
	-sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
	-sudo -u postgres createdb inbox
	-sudo -u postgres createdb inbox_test

seed:             ## (re)create schema + 2 workspaces x 2 agents + sample conversations
	cd backend && .venv/bin/python scripts/setup_db.py --demo

test:             ## full test suite against inbox_test
	cd backend && .venv/bin/pytest

api:              ## FastAPI on :8000 (also serves frontend/dist if built)
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

ui:               ## Vite dev server on :5173 (proxies /api -> :8000)
	cd frontend && npm run dev

build:
	cd frontend && npm run build

simulate:         ## stream webhook traffic (out-of-order + duplicates) at :8000
	cd backend && .venv/bin/python scripts/simulate.py
