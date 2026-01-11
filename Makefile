.PHONY: help install run test test-cov lint format check clean docker-setup docker-build docker-run docker-push k8s-deploy k8s-logs k8s-manual k8s-status k8s-delete

help:
	@echo "trakt-toggl-sync - Available Commands"
	@echo ""
	@echo "Development:"
	@echo "  make install      Install dependencies"
	@echo "  make run          Run the sync script"
	@echo "  make test         Run tests"
	@echo "  make test-cov     Run tests with coverage"
	@echo "  make lint         Run linter (ruff check)"
	@echo "  make format       Format code (ruff format)"
	@echo "  make check        Run lint + format check"
	@echo "  make clean        Clean cache and temp files"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-setup Setup buildx for multi-platform (one-time)"
	@echo "  make docker-build Build Docker image (local arch only)"
	@echo "  make docker-run   Run with docker-compose"
	@echo "  make docker-push  Build & push multi-platform to Docker Hub"
	@echo ""
	@echo "Kubernetes:"
	@echo "  make k8s-deploy   Deploy to Kubernetes"
	@echo "  make k8s-logs     View logs from latest job"
	@echo "  make k8s-manual   Manually trigger a sync"
	@echo "  make k8s-status   Show K8s status"
	@echo "  make k8s-delete   Delete all K8s resources"

install:
	@echo "Installing dependencies..."
	uv sync --all-groups

run:
	@echo "Running sync..."
	uv run src/sync.py

test:
	@echo "Running tests..."
	uv run --group dev pytest tests/ -v

test-cov:
	@echo "Running tests with coverage..."
	uv run --group dev pytest tests/ -v --cov=src --cov-report=html --cov-report=term
	@echo "Coverage report: htmlcov/index.html"

lint:
	@echo "Running linter..."
	uv run --group dev ruff check src/ tests/

format:
	@echo "Formatting code..."
	uv run --group dev ruff format src/ tests/

check: lint
	@echo "Checking format..."
	uv run --group dev ruff format --check src/ tests/

clean:
	@echo "Cleaning up..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name ".DS_Store" -delete
	rm -rf htmlcov .coverage .pytest_cache .ruff_cache
	@echo "Clean complete"

docker-setup:
	@echo "Setting up Docker buildx for multi-platform builds..."
	@if ! docker buildx ls | grep -q "multiplatform"; then \
		echo "Creating multiplatform builder..."; \
		docker buildx create --name multiplatform --driver docker-container --use --bootstrap; \
	else \
		echo "Multiplatform builder already exists"; \
		docker buildx use multiplatform; \
	fi
	@echo "Builder ready. Run 'docker buildx ls' to verify."

docker-build:
	@echo "Building Docker image for local architecture..."
	docker buildx build --load -t trakt-toggl-sync:latest .

docker-run:
	@echo "Running with docker-compose..."
	docker-compose up

docker-push:
	@echo "Building and pushing multi-platform images to Docker Hub..."
	@if ! docker buildx ls | grep -q "multiplatform"; then \
		echo "Error: buildx not set up. Run 'make docker-setup' first."; \
		exit 1; \
	fi
	@read -p "Enter Docker Hub username: " USERNAME; \
	echo ""; \
	echo "Logging in to Docker Hub..."; \
	docker login; \
	echo ""; \
	echo "Building for linux/amd64 and linux/arm64..."; \
	docker buildx build \
		--platform linux/amd64,linux/arm64 \
		-t $USERNAME/trakt-toggl-sync:latest \
		--push \
		.; \
	echo ""; \
	echo "✓ Pushed to: $USERNAME/trakt-toggl-sync:latest"

k8s-deploy:
	@echo "Deploying to Kubernetes..."
	kubectl apply -f k8s/base/namespace.yaml
	kubectl apply -f k8s/secrets/
	kubectl apply -f k8s/base/cronjob.yaml
	@echo "Deployed"

k8s-logs:
	@echo "Fetching logs from latest job..."
	@LATEST_JOB=$(kubectl get jobs -n trakt-toggl --sort-by=.metadata.creationTimestamp -o name | tail -1); \
	if [ -z "$LATEST_JOB" ]; then \
		echo "No jobs found"; \
	else \
		kubectl logs -n trakt-toggl $LATEST_JOB --tail=100; \
	fi

k8s-manual:
	@echo "Triggering manual sync..."
	kubectl create job -n trakt-toggl --from=cronjob/trakt-toggl-sync manual-sync-$$(date +%s)

k8s-status:
	@echo "Kubernetes Status:"
	@echo ""
	@echo "CronJob:"
	@kubectl get cronjobs -n trakt-toggl
	@echo ""
	@echo "Recent Jobs:"
	@kubectl get jobs -n trakt-toggl --sort-by=.metadata.creationTimestamp | tail -5

k8s-delete:
	@echo "Deleting Kubernetes resources..."
	@read -p "Delete namespace trakt-toggl? (yes/no): " CONFIRM; \
	if [ "$CONFIRM" = "yes" ]; then \
		kubectl delete namespace trakt-toggl; \
		echo "Deleted"; \
	else \
		echo "Cancelled"; \
	fi
