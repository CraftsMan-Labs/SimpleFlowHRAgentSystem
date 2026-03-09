.PHONY: help up down ps logs test-backend build-frontend smoke

help:
	@printf "SimpleFlowHRAgentSystem targets:\n"
	@printf "  make up            # start backend/frontend in Docker\n"
	@printf "  make down          # stop Docker services\n"
	@printf "  make ps            # show running containers\n"
	@printf "  make logs          # stream Docker service logs\n"
	@printf "  make test-backend  # run backend unit tests\n"
	@printf "  make build-frontend# build frontend bundle in Docker\n"
	@printf "  make smoke         # health/meta/invoke smoke checks\n"

up:
	docker compose up -d

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f --tail=120

test-backend:
	docker compose run --rm hr-backend python -m unittest -v test_runtime_template.py

build-frontend:
	docker compose run --rm hr-frontend npm run build

smoke:
	curl -sS http://localhost:8092/health
	curl -sS http://localhost:8092/meta
	curl -sS -X POST http://localhost:8092/invoke -H "Content-Type: application/json" -d '{"schema_version":"v1","run_id":"run-smoke-1","agent_id":"hr-agent-runtime","agent_version":"v1","mode":"realtime","trace":{"trace_id":"trace1","span_id":"span1","tenant_id":"dev-org"},"input":{"message":"Draft an HR warning email for repeated delays"},"deadline_ms":0,"idempotency_key":"smoke-1"}'
