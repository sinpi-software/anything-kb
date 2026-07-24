#!/usr/bin/env bash
# Runs ON the k3s node (invoked by .github/workflows/deploy.yml over the tunnel, or by
# hand). Builds the engine image into the node's local registry, then `pulumi up` — the
# registry, k3s API, and Pulumi state all live locally on the node.
#
#   Usage:  ./deploy.sh [stack] [image-tag]
set -euo pipefail

STACK="${1:-home}"
TAG="${2:-$(date +%s)}"
HERE="$(cd "$(dirname "$0")" && pwd)"   # ~/anything-kb/deploy
cd "$HERE"

export PATH="$HOME/.pulumi/bin:$PATH"
# Passphrase + stack config persist on the node (never synced from CI).
[ -f "$HERE/.passphrase" ] && export PULUMI_CONFIG_PASSPHRASE_FILE="$HERE/.passphrase"
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

echo ">> build + push localhost:5000/anything-ingestion:$TAG"
docker build -q -t "localhost:5000/anything-ingestion:$TAG" "$HERE/../ingestion" >/dev/null
docker push "localhost:5000/anything-ingestion:$TAG" >/dev/null

echo ">> pulumi up ($STACK)"
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt
pulumi stack select "$STACK"
pulumi config set image "localhost:5000/anything-ingestion:$TAG"
pulumi up --yes --stack "$STACK"
echo ">> deployed :$TAG"
