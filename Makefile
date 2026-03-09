.PHONY: help setup backend-env frontend-env test-backend build-frontend run-backend run-frontend smoke

help:
	@printf "SimpleFlowHRAgentSystem targets:\n"
	@printf "  make setup         # install backend and frontend dependencies\n"
	@printf "  make test-backend  # run backend unit tests\n"
	@printf "  make build-frontend# build frontend bundle\n"
	@printf "  make run-backend   # start backend on :8091\n"
	@printf "  make run-frontend  # start frontend on :5173\n"
	@printf "  make smoke         # health/meta/invoke smoke checks\n"

setup:
	python -m venv backend/.venv
	backend/.venv/bin/pip install fastapi uvicorn PyJWT
	backend/.venv/bin/pip install -e ../../SimpleFlowSDKs/python
	backend/.venv/bin/pip install -e ../../SimpleAgents/crates/simple-agents-py
	npm --prefix frontend install

test-backend:
	python -m unittest -v backend/test_runtime_template.py

build-frontend:
	npm --prefix frontend run build

run-backend:
	backend/.venv/bin/uvicorn app:app --app-dir backend --host 0.0.0.0 --port 8091 --reload

run-frontend:
	npm --prefix frontend run dev

smoke:
	curl -sS http://localhost:8091/health
	curl -sS http://localhost:8091/meta
	curl -sS -X POST http://localhost:8091/invoke -H "Content-Type: application/json" -d '{"schema_version":"v1","run_id":"run-smoke-1","agent_id":"hr-agent-runtime","agent_version":"v1","mode":"realtime","trace":{"trace_id":"trace1","span_id":"span1","tenant_id":"dev-org"},"input":{"message":"Draft an HR warning email for repeated delays"},"deadline_ms":0,"idempotency_key":"smoke-1"}'
