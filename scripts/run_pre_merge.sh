#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$#" -ne 1 ]; then
  echo "usage: scripts/run_pre_merge.sh <pull-request-number>" >&2
  exit 2
fi
if [ "${AGENT_CI_MERGE_AUTHORIZED:-false}" != "true" ]; then
  echo "main merge authorization is required; set AGENT_CI_MERGE_AUTHORIZED=true after explicit user instruction" >&2
  exit 2
fi
if ! grep -Eq '^  enabled: true$' "$ROOT_DIR/.agent-ci-policy.yml"; then
  echo "GitHub Actions is disabled. Set github.enabled: true after creating the repository." >&2
  exit 2
fi
command -v gh >/dev/null 2>&1 || { echo "GitHub CLI (gh) is required" >&2; exit 127; }

PR_NUMBER="$1"
BASE_BRANCH="$(gh pr view "$PR_NUMBER" --json baseRefName --jq .baseRefName)"
SOURCE_BRANCH="$(gh pr view "$PR_NUMBER" --json headRefName --jq .headRefName)"
PR_SHA="$(gh pr view "$PR_NUMBER" --json headRefOid --jq .headRefOid)"
LOCAL_SHA="$(git -C "$ROOT_DIR" rev-parse HEAD)"

test "$BASE_BRANCH" = main || { echo "pull request must target main" >&2; exit 2; }
test "$SOURCE_BRANCH" != main || { echo "source branch must be a child branch, not main" >&2; exit 2; }
test "$LOCAL_SHA" = "$PR_SHA" || { echo "local HEAD does not match the pull request commit" >&2; exit 2; }

bash "$ROOT_DIR/scripts/run_ci_local_quiet.sh"
gh workflow run pre-merge.yml \
  --ref "$SOURCE_BRANCH" \
  --field local_ci_passed=true \
  --field merge_authorized=true \
  --field source_branch="$SOURCE_BRANCH" \
  --field target_branch=main
echo "pre-merge workflow dispatched once for PR #$PR_NUMBER"
