.PHONY: help up down logs pull build status clean check-gpu

help: ## Show this help message
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

up: ## Start all services
	docker compose up -d

down: ## Stop all services
	docker compose down

logs: ## View logs (follow mode, last 200 lines)
	docker compose logs -f --tail=200

pull: ## Pull latest images
	docker compose pull

build: ## Build custom images
	docker compose build

status: ## Show service status
	docker compose ps

clean: ## Remove stopped containers, dangling images, and local cache (requires CONFIRM=yes)
	@if [ "$(CONFIRM)" != "yes" ]; then \
		echo "WARNING: This will remove stopped containers, dangling images, and local outputs cache."; \
		echo "Run with CONFIRM=yes to proceed."; \
		exit 1; \
	fi
	docker compose down --remove-orphans
	docker image prune -f
	@echo "Cleaned up Docker resources. Local outputs preserved."

check-gpu: ## Validate GPU access in Docker
	@./scripts/check_gpu_docker.sh
