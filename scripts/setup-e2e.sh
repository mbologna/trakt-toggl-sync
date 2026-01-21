#!/bin/bash
# Setup script for E2E tests
# This script helps configure E2E test environment variables

set -e

echo "===================================="
echo "E2E Test Environment Setup"
echo "===================================="
echo ""

# Check if .trakt_tokens.json exists
if [ ! -f ".trakt_tokens.json" ]; then
    echo "❌ .trakt_tokens.json not found"
    echo ""
    echo "You need to authenticate with Trakt first:"
    echo "  1. Run: make run"
    echo "  2. Complete the browser authentication"
    echo "  3. This will create .trakt_tokens.json"
    echo "  4. Run this script again"
    echo ""
    exit 1
fi

echo "✓ Found .trakt_tokens.json"
echo ""

# Check if jq is installed
if ! command -v jq &> /dev/null; then
    echo "⚠ jq is not installed (needed to parse JSON)"
    echo ""
    echo "Install jq:"
    echo "  macOS: brew install jq"
    echo "  Ubuntu: sudo apt-get install jq"
    echo "  Or manually extract tokens from .trakt_tokens.json"
    echo ""
    exit 1
fi

# Extract tokens
ACCESS_TOKEN=$(jq -r .access_token .trakt_tokens.json)
REFRESH_TOKEN=$(jq -r .refresh_token .trakt_tokens.json)

# Load .env file if it exists
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

echo "===================================="
echo "Copy these to your shell or CI/CD:"
echo "===================================="
echo ""
echo "# Trakt credentials"
echo "export E2E_TRAKT_CLIENT_ID=\"${TRAKT_CLIENT_ID:-YOUR_CLIENT_ID}\""
echo "export E2E_TRAKT_CLIENT_SECRET=\"${TRAKT_CLIENT_SECRET:-YOUR_CLIENT_SECRET}\""
echo "export E2E_TRAKT_ACCESS_TOKEN=\"${ACCESS_TOKEN}\""
echo "export E2E_TRAKT_REFRESH_TOKEN=\"${REFRESH_TOKEN}\""
echo ""
echo "# Toggl credentials"
echo "export E2E_TOGGL_API_TOKEN=\"${TOGGL_API_TOKEN:-YOUR_API_TOKEN}\""
echo "export E2E_TOGGL_WORKSPACE_ID=\"${TOGGL_WORKSPACE_ID:-YOUR_WORKSPACE_ID}\""
echo "export E2E_TOGGL_PROJECT_ID=\"${TOGGL_PROJECT_ID:-YOUR_PROJECT_ID}\""
echo ""
echo "# Enable E2E tests"
echo "export E2E_TEST_ENABLED=true"
echo ""
echo "===================================="
echo ""
echo "To run E2E tests:"
echo "  1. Copy the exports above"
echo "  2. Paste them in your terminal"
echo "  3. Run: make test-e2e"
echo ""
echo "Or run directly with:"
echo "  E2E_TEST_ENABLED=true E2E_TRAKT_CLIENT_ID=... make test-e2e"
echo ""
