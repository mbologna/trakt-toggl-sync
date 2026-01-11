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

## Quick Start

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

## Prerequisites

**API Credentials:**
- Trakt API: https://trakt.tv/oauth/applications
- Toggl API: https://track.toggl.com/profile

**Runtime:**
- Python 3.14+ with [uv](https://github.com/astral-sh/uv)
- Docker (optional)
- Kubernetes (optional)

## Configuration

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

## Usage

### Local Development

```bash
make install    # Install dependencies
make run        # Run sync
make test       # Run tests
make lint       # Check code
make format     # Format code
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

**Important: Authenticate locally before deploying to K8s**

```bash
# Step 1: Run locally to authenticate with Trakt
make run
# Complete the browser authentication - this creates .trakt_tokens.json

# Step 2: Create namespace
kubectl create namespace trakt-toggl

# Step 3: Create token secret from your local token file
kubectl create secret generic trakt-tokens \
  --from-file=.trakt_tokens.json=.trakt_tokens.json \
  --namespace=trakt-toggl

# Step 4: Setup other secrets
cp k8s/base/configmap-template.yaml k8s/secrets/configmap.yaml
cp k8s/base/secret-template.yaml k8s/secrets/secret.yaml
# Edit k8s/secrets/*.yaml with your values

# Step 5: Apply secrets
kubectl apply -f k8s/secrets/configmap.yaml
kubectl apply -f k8s/secrets/secret.yaml

# Step 6: Deploy CronJob
kubectl apply -f k8s/base/cronjob.yaml

# Step 7: Verify
make k8s-status
make k8s-logs

# Manually trigger a sync
make k8s-manual
```

## How It Works

1. **Deduplicate Trakt** - Removes duplicate watch history
2. **Deduplicate Toggl** - Removes duplicate time entries
3. **Sync** - Creates Toggl entries for recent Trakt history (default: 7 days)

Rate limits are handled gracefully—if Toggl returns 402, deduplication is skipped and sync continues.

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

## Development

```bash
# Lint and format
make check

# Run tests with coverage
make test-cov

# Clean cache
make clean
```

## Troubleshooting

**Rate Limiting (402 Error)**
- Handled gracefully—sync continues
- Deduplication skipped temporarily
- Try again in a few minutes

**Token Expired**
```bash
rm .trakt_tokens.json
make run  # Re-authenticate
```

**No Entries Syncing**
- Verify `TOGGL_PROJECT_ID`
- Check logs for "Skipped (exists)"
- Adjust `TRAKT_HISTORY_DAYS`

## License

See [LICENSE](LICENSE)
