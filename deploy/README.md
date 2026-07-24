# Deploying the knowledge-graph engine to k3s

Pulumi (Python) program that stands up the whole engine on a **single-node k3s**
cluster: an in-cluster registry, Postgres 16, Neo4j 5, the API + worker, an
Alembic migration job, and a Cloudflare Tunnel.

```
node-trust (SSH) → registry → build+push image → postgres + neo4j
                 → migrate (alembic) → api + worker → cloudflared
```

## Prerequisites

On the **machine you run `pulumi up` from**:

- Docker (builds the image) — and it must allow pushing to the insecure LAN
  registry. Add to `/etc/docker/daemon.json` and restart Docker:
  ```json
  { "insecure-registries": ["NODE_HOST:30500"] }
  ```
- `kubectl` context / `KUBECONFIG` pointing at the k3s cluster.
- SSH access to the node as a user with **passwordless sudo** (used once to write
  `/etc/rancher/k3s/registries.yaml` and restart k3s). Use a passphrase-less key.
- Pulumi CLI, and a self-hosted state backend: `pulumi login --local` (state in
  `~/.pulumi`) or `pulumi login file://./state`.

In the **Cloudflare dashboard** (Zero Trust → Networks → Tunnels): create a
tunnel, copy its **token**, and add a public hostname route pointing at
`http://ingestion-api.ingestion.svc.cluster.local:80`.

## Configure

```bash
cd deploy
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
pulumi stack init home                 # creates the stack + a state passphrase

pulumi config set nodeHost 192.168.1.50          # your k3s node IP/hostname
pulumi config set sshUser steve
pulumi config set sshPrivateKeyPath ~/.ssh/id_ed25519
# pulumi config set registryNodePort 30500       # optional (default 30500)
# pulumi config set imageTag v1                   # optional (default "latest")

pulumi config set --secret pgPassword       "$(openssl rand -hex 16)"   # keep alphanumeric/URL-safe
pulumi config set --secret neo4jPassword    "$(openssl rand -hex 16)"
pulumi config set --secret openrouterApiKey "sk-or-..."
pulumi config set --secret tunnelToken      "<cloudflare tunnel token>"
```

> Passwords go straight into a Postgres connection URL, so use URL-safe values
> (hex/alphanumeric). `openssl rand -hex 16` is fine.

## Deploy

```bash
pulumi up
```

The first `pulumi up` briefly restarts k3s (to load the registry trust), then
brings everything up in dependency order. On a rebuild, bump `imageTag` (or rely
on the digest changing) and re-run `pulumi up` — the API/worker roll to the new
image automatically.

## First API key

Seeding is a manual, on-demand step (it mints an org + prints an API key once):

```bash
kubectl -n ingestion exec deploy/ingestion-api -- python seed.py
```

Copy the printed key — it's the credential for `POST /content`, `PUT /config`,
and `/graphql` through the tunnel.

## Smoke test

```bash
KEY=<the key>
HOST=https://ingestion.your-domain.com          # your tunnel hostname
curl -s -X PUT $HOST/config -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"relevance_prompt":"Tech companies and their people.","entity_types":["Person","Organization"],"relationship_types":["WORKS_AT","LEADS"]}'
curl -s -X POST $HOST/content -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"text":"OpenAI, led by Sam Altman, released GPT-5."}'
# poll GET /content/{job_id}, then:
curl -s -X POST $HOST/graphql -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"query":"{ nodes { type name edges { type target { name } } } }"}'
```

## Notes

- **Single-node only.** `local-path` PVCs are node-local; a multi-node cluster
  would need the DB/registry pods pinned to the node holding their data.
- **Insecure registry** (HTTP) — fine on a trusted LAN; nothing else reaches
  `:30500`. The node trusts it via `registries.yaml`; your build host trusts it
  via `insecure-registries`.
- **Tear down:** `pulumi destroy` (the PVCs, and thus the data, go with it).
