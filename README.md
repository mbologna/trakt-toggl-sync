# trakt-toggl-sync

> Automatically sync Trakt viewing history to Toggl for complete time tracking

## Motivation

I use Toggl for holistic time management—tracking work, projects, and personal activities. However, entertainment time was missing from this picture.

By syncing my Trakt viewing history to Toggl, I now have:
- **Complete time tracking**: Work, hobbies, AND entertainment in one place
- **Better insights**: Understand actual free time vs. perceived free time
- **Accurate reporting**: No gaps in my daily timeline
- **Effortless tracking**: No manual entry for movies and TV shows

This bridges productivity tracking with leisure tracking for a truly comprehensive view of my time.

## Features

- ✅ Auto-deduplication (Trakt and Toggl)
- ✅ Smart syncing with caching
- ✅ Automatic token refresh
- ✅ Graceful rate limit handling
- ✅ Multiple deployment options (local, Docker, Kubernetes)

## How It Works

1. **Deduplicate Trakt** - Removes duplicate watch history entries
2. **Deduplicate Toggl** - Removes duplicate time entries
3. **Sync** - Creates Toggl entries for recent Trakt history (default: 7 days)

Rate limits are handled gracefully—if Toggl returns 402, deduplication is skipped and sync continues.

The Kubernetes CronJob automatically refreshes OAuth tokens before they expire, ensuring uninterrupted syncing.

## Example Output

```
[2026-01-10 12:47:51] ===== Starting trakt-toggl-sync =====

[2026-01-10 12:47:51] === Step 1: Removing Trakt Duplicates ===
[2026-01-10 12:47:52] Found 6 duplicate entries
[2026-01-10 12:47:52] ✓ Successfully removed 6 duplicates

[2026-01-10 12:47:53] === Step 2: Removing Toggl Duplicates ===
[2026-01-10 12:47:53] ⚠ Toggl rate limit reached. Skipping deduplication.

[2026-01-10 12:47:54] === Step 3: Syncing Trakt to Toggl ===
[2026-01-10 12:47:55] ✓ Created: 📺 The Office - S03E15 (at 2025-12-30 20:00)
[2026-01-10 12:47:56] Skipped (exists): 🎞️ Inception (2010)

[2026-01-10 12:48:00] ===== Sync Complete =====
```

## Prerequisites

**API Credentials:**
- Trakt API: https://trakt.tv/oauth/applications
- Toggl API: https://track.toggl.com/profile

**Runtime:**
- Python 3.14+ with [uv](https://github.com/astral-sh/uv)
- Docker (optional)
- Kubernetes (optional)

## Usage

### Quick Start

```bash
# Clone and setup
git clone https://github.com/YOUR_USERNAME/trakt-toggl-sync.git
cd trakt-toggl-sync
cp .env.template .env
# Edit .env with your API credentials

# Run locally
make run

# Or with Docker
make docker-run
```

### Configuration

Edit `.env`:

```env
# Trakt
TRAKT_CLIENT_ID=your_client_id
TRAKT_CLIENT_SECRET=your_client_secret
TRAKT_HISTORY_DAYS=7

# Toggl
TOGGL_API_TOKEN=your_api_token
TOGGL_WORKSPACE_ID=your_workspace_id
TOGGL_PROJECT_ID=your_project_id
TOGGL_TAGS=watching,entertainment
```

### Docker

```bash
# One-time setup for multi-platform builds
make docker-setup

# Build for local architecture
make docker-build

# Run locally
make docker-run

# Build and push multi-platform (amd64 + arm64) to Docker Hub
make docker-push
```

### Kubernetes

The Kubernetes deployment uses a CronJob that runs every 6 hours with persistent storage for Trakt OAuth tokens.

#### Initial Setup

**Step 1: Prepare Kubernetes configuration**

```bash
# Create namespace
kubectl apply -f k8s/base/namespace.yaml

# Copy and edit configuration templates
cp k8s/base/configmap-template.yaml k8s/secrets/configmap.yaml
cp k8s/base/secret-template.yaml k8s/secrets/secret.yaml

# Edit k8s/secrets/configmap.yaml with your Toggl IDs
# Edit k8s/secrets/secret.yaml with base64-encoded API credentials:
echo -n "your_trakt_client_id" | base64
echo -n "your_trakt_client_secret" | base64
echo -n "your_toggl_api_token" | base64
```

**Step 2: Deploy to Kubernetes**

```bash
# Apply PersistentVolumeClaim for token storage
kubectl apply -f k8s/base/pvc.yaml

# Apply configuration and secrets
kubectl apply -f k8s/secrets/configmap.yaml
kubectl apply -f k8s/secrets/secret.yaml

# Deploy the CronJob
kubectl apply -f k8s/base/cronjob.yaml
```

**Step 3: Initial token setup (first time only)**

Since the CronJob won't have authentication tokens yet, you have to manually trigger the first job and authenticate interactively:

```bash
# Create a one-time job from the cronjob
kubectl create job --from=cronjob/trakt-toggl-sync trakt-sync-initial -n trakt-toggl

# Watch the logs
kubectl logs -f job/trakt-sync-initial -n trakt-toggl

# Follow the authentication URL shown in the logs
# Once authenticated, the token will be saved to the PVC
```

#### Automation via CronJob

The Kubernetes deployment uses a CronJob that runs every 6 hours with persistent storage for Trakt OAuth tokens.

##### Management Commands

```bash
# Check CronJob status
kubectl get cronjob -n trakt-toggl

# View recent job runs
kubectl get jobs -n trakt-toggl

# View logs from latest run
kubectl logs -l app=trakt-toggl-sync -n trakt-toggl --tail=100

# Manually trigger a sync
kubectl create job --from=cronjob/trakt-toggl-sync trakt-sync-manual-$(date +%s) -n trakt-toggl

# Check PVC status
kubectl get pvc -n trakt-toggl

# Delete everything
kubectl delete namespace trakt-toggl
```

#### Storage Configuration

The deployment uses a PersistentVolumeClaim (PVC) to store Trakt OAuth tokens. By default, it requests 3Mi of storage.

## Troubleshooting

### Rate Limiting (402 Error)
- Handled gracefully—sync continues
- Deduplication skipped temporarily
- Try again in a few minutes

### Token Expired
```bash
# Local
rm .trakt_tokens.json
make run  # Re-authenticate

# Kubernetes
kubectl delete pvc trakt-tokens-pvc -n trakt-toggl
kubectl apply -f k8s/pvc.yaml
# Then follow Step 4 again to re-authenticate
```

### No Entries Syncing
- Verify `TOGGL_PROJECT_ID` is correct
- Check logs for "Skipped (exists)" messages
- Adjust `TRAKT_HISTORY_DAYS` to sync more history
- Ensure entries exist in Trakt for the specified time period

### Kubernetes Pod Crashes
```bash
# Check pod logs
kubectl logs -l app=trakt-toggl-sync -n trakt-toggl

# Check pod events
kubectl describe pod -l app=trakt-toggl-sync -n trakt-toggl

# Verify secrets are created correctly
kubectl get secrets -n trakt-toggl
kubectl describe secret trakt-toggl-credentials -n trakt-toggl
```

### PVC Not Mounting
- Verify your cluster has a storage provisioner installed
- Check available storage classes: `kubectl get storageclass`
- Update `k8s/pvc.yaml` with your cluster's storage class

## Development

```bash
make install    # Install dependencies
make run        # Run sync
make test       # Run tests
make lint       # Check code
make format     # Format code
make check      # Lint and format
make test-cov   # Run tests with coverage
make clean      # Clean cache
```

### End-to-End Testing

The project includes comprehensive E2E tests that verify integration with real Trakt and Toggl APIs.

**Setup E2E Tests:**

```bash
# 1. Authenticate with Trakt (creates .trakt_tokens.json)
make run

# 2. Get setup instructions
make test-e2e-setup

# 3. Export the environment variables shown (example):
export E2E_TRAKT_CLIENT_ID="your_client_id"
export E2E_TRAKT_CLIENT_SECRET="your_client_secret"
export E2E_TRAKT_ACCESS_TOKEN="token_from_json"
export E2E_TRAKT_REFRESH_TOKEN="token_from_json"
export E2E_TOGGL_API_TOKEN="your_api_token"
export E2E_TOGGL_WORKSPACE_ID="123456"
export E2E_TOGGL_PROJECT_ID="789012"
export E2E_TEST_ENABLED=true

# 4. Run E2E tests
make test-e2e
```

**Note:** E2E tests will create temporary test entries in your Toggl project. These are tagged with `e2e-test` and should be cleaned up manually.

### Pre-commit Hooks

Install pre-commit hooks to automatically check code quality before commits:

```bash
make pre-commit-install

# Run hooks manually
make pre-commit-run

# Update hooks to latest versions
make pre-commit-update
```

Hooks include:
- Ruff linting and formatting
- YAML/TOML syntax checking
- Trailing whitespace removal
- Security vulnerability scanning (Bandit)
- Docstring validation
