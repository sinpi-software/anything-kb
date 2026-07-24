# Deploying the knowledge-graph engine to k3s (`skynet`)

Pulumi (Python) program that deploys the engine onto the single-node k3s cluster:
Postgres 16, Neo4j 5, the API + worker, an Alembic migration job, and (optionally)
a Cloudflare Tunnel.

The cluster **already has a registry** (docker `registry:2` on the node at `:5000`,
trusted by containerd as `localhost:5000`). So Pulumi does **not** manage a registry
or touch `registries.yaml` — the image is built+pushed out of band and referenced by
tag. Pulumi only deploys the workloads.

```
build+push image (on the node) → pulumi up:
  namespace + secrets → postgres + neo4j → migrate (alembic) → api + worker → [cloudflared]
```

## One-time prerequisites

- **kubeconfig** for the cluster, reachable from where you run Pulumi:
  ```bash
  ssh egeste@192.168.0.202 'sudo cat /etc/rancher/k3s/k3s.yaml' \
    | sed 's#https://127.0.0.1:6443#https://192.168.0.202:6443#' > deploy/kubeconfig
  ```
- Pulumi CLI + self-hosted state: `pulumi login --local` and a `PULUMI_CONFIG_PASSPHRASE`.
- The Python deps: `cd deploy && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt`.

## 1. Build + push the image (on the node)

Docker and the registry live on the node, so build there — no local Docker config needed:

```bash
SHA=$(git rev-parse --short HEAD)
rsync -az --delete --exclude='.venv' --exclude='__pycache__' --exclude='.env*' \
  ingestion/ egeste@192.168.0.202:/tmp/ingestion-build/
ssh egeste@192.168.0.202 "cd /tmp/ingestion-build && \
  docker build -t localhost:5000/anything-ingestion:$SHA . && \
  docker push localhost:5000/anything-ingestion:$SHA"
```

## 2. Configure the stack

```bash
cd deploy
export PATH=$HOME/.pulumi/bin:$PATH
export PULUMI_CONFIG_PASSPHRASE=$(cat .passphrase)     # created at stack init
export KUBECONFIG=$(pwd)/kubeconfig

pulumi stack select home     # or: pulumi stack init home
pulumi config set image localhost:5000/anything-ingestion:$SHA
pulumi config set --secret pgPassword       "$(openssl rand -hex 16)"   # URL-safe
pulumi config set --secret neo4jPassword    "$(openssl rand -hex 16)"
pulumi config set --secret openrouterApiKey "sk-or-..."                 # or pull from ../.env
# optional — omit to skip the tunnel:
# pulumi config set --secret tunnelToken "<cloudflare tunnel token>"
```

## 3. Deploy

```bash
pulumi up --yes
```

Pulumi waits for Postgres, then the migration Job, then brings up the API + worker
(which wait on Neo4j). Re-deploying a new image: rebuild+push with a new `$SHA`,
`pulumi config set image ...`, `pulumi up`.

## 4. First API key

```bash
kubectl -n ingestion exec deploy/ingestion-api -- python seed.py
```

Copy the printed key — it authenticates `POST /content`, `PUT /config`, `/graphql`.

## Reaching the API

- **Without a tunnel:** `kubectl -n ingestion port-forward svc/ingestion-api 8080:80`
  then hit `http://localhost:8080`.
- **With the Cloudflare Tunnel:** set the connector token and, since this tunnel
  routes its hostname to Traefik (hub pattern), point an Ingress at the API:
  ```bash
  pulumi config set --secret tunnelToken "eyJ..."       # connector token, not a tunnel ID
  pulumi config set apiHostname desk.sinpi.software      # the tunnel's public hostname
  pulumi up
  ```
  Live at `https://desk.sinpi.software` (Cloudflare → tunnel → Traefik → Ingress → API).
  If instead your tunnel routes a hostname *directly* to a service, point its
  dashboard route at `http://ingestion-api.ingestion.svc.cluster.local:80` and skip
  `apiHostname`.

## Notes

- **Single-node only** — `local-path` PVCs are node-local.
- `kubeconfig` and `.passphrase` are gitignored — they hold cluster creds / the
  state-encryption key. Keep them safe.
- **Tear down:** `pulumi destroy` (PVCs and their data go with it).
