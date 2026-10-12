#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

git diff --quiet
git diff --cached --quiet
test -z "$(git status --porcelain --untracked-files=normal | grep -v '^?? data/' || true)"

npm --prefix webui run build
release_sha="$(git rev-parse HEAD)"
output_dir="$repo_root/dist/release"
mkdir -p "$output_dir"
package="$output_dir/ai-daq-fae-${release_sha}.tar.gz"

tar -czf "$package" \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
  daq_fae src scripts migrations prompts upstream-source.json \
  requirements.txt requirements-daq.txt deploy/Dockerfile.prod \
  deploy/compose.prod.yml deploy/provision_env.py webui/dist

printf '%s  %s\n' "$(shasum -a 256 "$package" | awk '{print $1}')" "$package"
