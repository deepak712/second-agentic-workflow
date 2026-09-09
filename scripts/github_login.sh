#!/bin/bash
# Authenticate gh using GITHUB_TOKEN from .env, then optionally create and push
# the repo. The token is piped straight into gh: it is never echoed, never passed
# as a command-line argument (which would expose it to `ps`), and never written
# to shell history.
#
#   ./scripts/github_login.sh            # log in only
#   ./scripts/github_login.sh --push     # log in, then create the private repo and push
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_NAME="second-agentic-workflow"

if ! command -v gh >/dev/null 2>&1; then
  echo "error: gh is not installed. Run: brew install gh" >&2
  exit 1
fi

if [ ! -f "$ROOT/.env" ]; then
  echo "error: $ROOT/.env not found." >&2
  exit 1
fi

# Read only the one line we need, and strip any accidental quotes or spaces.
TOKEN="$(grep -E '^GITHUB_TOKEN=' "$ROOT/.env" | head -1 | cut -d= -f2- | tr -d '"'\''[:space:]')"

if [ -z "$TOKEN" ]; then
  cat >&2 <<'MSG'
error: GITHUB_TOKEN is empty in .env

Create a CLASSIC token at https://github.com/settings/tokens/new
  - tick the top-level "repo" scope
  - copy it (starts with ghp_) and paste it after GITHUB_TOKEN= in .env
MSG
  exit 1
fi

case "$TOKEN" in
  ghp_*) ;;
  github_pat_*)
    # Fine-grained tokens cannot call createRepository unless they carry
    # account-level "Administration: write", which is off by default. Pushing to
    # an existing repo is fine, so this is only fatal when creating one.
    if [ "${1:-}" = "--push" ] && ! git -C "$ROOT" remote get-url origin >/dev/null 2>&1; then
      cat >&2 <<'MSG'
warning: this is a FINE-GRAINED token (github_pat_...).

Fine-grained tokens cannot create repositories, so `gh repo create` will fail
with "Resource not accessible by personal access token". Two ways forward:

  A) Use a CLASSIC token instead - simplest, works for create and push:
     https://github.com/settings/tokens/new  -> tick the top-level "repo" scope

  B) Create the empty repo yourself at https://github.com/new (private, no
     README), then re-run this script. Your token must have been issued with
     "All repositories" access, or it will not be able to push to the new one.

Continuing anyway in case the token has Administration: write...
MSG
    fi
    ;;
  *) echo "warning: that does not look like a GitHub token (expected ghp_... )" >&2 ;;
esac

printf '%s' "$TOKEN" | gh auth login --with-token
unset TOKEN

echo "logged in as: $(gh api user --jq .login)"
gh auth setup-git

if [ "${1:-}" != "--push" ]; then
  echo
  echo "Next: ./scripts/github_login.sh --push"
  exit 0
fi

cd "$ROOT"
if git remote get-url origin >/dev/null 2>&1; then
  echo "remote 'origin' already exists -> $(git remote get-url origin)"
  git push -u origin "$(git branch --show-current)"
else
  gh repo create "$REPO_NAME" --private --source=. --remote=origin --push
fi

echo
echo "pushed: $(gh repo view --json url --jq .url)"
echo "Revoke the token at https://github.com/settings/tokens when you are done,"
echo "or leave it in .env (gitignored) if you want to keep pushing."
