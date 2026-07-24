"""Deploy the knowledge-graph engine onto a single-node k3s cluster.

Order of operations (each step gates the next via depends_on):
  1. node-trust  — SSH to the node, write /etc/rancher/k3s/registries.yaml so
     containerd trusts the in-cluster (insecure, HTTP) registry, restart k3s,
     wait for the API to come back. All k8s resources use a provider that
     depends on this, so nothing touches the cluster until k3s is healthy.
  2. registry    — registry:2 + PVC + NodePort service.
  3. image       — build ingestion/ and push to the registry.
  4. postgres / neo4j — Deployment + local-path PVC + ClusterIP service each.
  5. migrate     — a Job running `alembic upgrade head` (gates api/worker).
  6. api / worker — the engine, wired to the DBs + secrets via envFrom.
  7. cloudflared — the Cloudflare Tunnel, routing your hostname to the API svc.
"""

from pathlib import Path

import pulumi
import pulumi_command as command
import pulumi_docker as docker
import pulumi_kubernetes as k8s

NS = "ingestion"
cfg = pulumi.Config()

# --- inputs ---------------------------------------------------------------
node_host = cfg.require("nodeHost")  # IP/hostname of the k3s node (reachable on the LAN)
ssh_user = cfg.require("sshUser")
ssh_key_path = cfg.require("sshPrivateKeyPath")  # passphrase-less key (or a deploy key)
registry_port = cfg.get_int("registryNodePort") or 30500
image_tag = cfg.get("imageTag") or "latest"

pg_password = cfg.require_secret("pgPassword")  # keep URL-safe (alphanumeric)
neo4j_password = cfg.require_secret("neo4jPassword")
openrouter_key = cfg.require_secret("openrouterApiKey")
tunnel_token = cfg.require_secret("tunnelToken")

registry_ref = f"{node_host}:{registry_port}"
ssh_key = Path(ssh_key_path).expanduser().read_text()


# --- 1. node trust: configure the insecure registry mirror, restart k3s ----
registries_yaml = (
    f'mirrors:\n  "{registry_ref}":\n    endpoint:\n      - "http://{registry_ref}"\n'
)
trust_script = f"""set -e
sudo mkdir -p /etc/rancher/k3s
sudo tee /etc/rancher/k3s/registries.yaml >/dev/null <<'YAML'
{registries_yaml}YAML
sudo systemctl restart k3s
for _ in $(seq 1 60); do
  sudo k3s kubectl get --raw=/readyz >/dev/null 2>&1 && exit 0
  sleep 2
done
echo "k3s did not become ready after restart" >&2
exit 1
"""

node_trust = command.remote.Command(
    "registry-trust",
    connection=command.remote.ConnectionArgs(host=node_host, user=ssh_user, private_key=ssh_key),
    create=trust_script,
    update=trust_script,
    triggers=[trust_script],
)

# Every k8s resource goes through this provider, which is gated on node-trust —
# so the cluster is only touched after k3s has restarted and gone ready.
k8s_provider = k8s.Provider("k3s", opts=pulumi.ResourceOptions(depends_on=[node_trust]))
k8s_opts = lambda deps=None: pulumi.ResourceOptions(provider=k8s_provider, depends_on=deps or [])  # noqa: E731

namespace = k8s.core.v1.Namespace(
    "ns", metadata={"name": NS}, opts=k8s_opts()
)


def meta(name: str) -> dict:
    return {"name": name, "namespace": NS}


# --- 2. in-cluster registry -----------------------------------------------
registry_pvc = k8s.core.v1.PersistentVolumeClaim(
    "registry-data",
    metadata=meta("registry-data"),
    spec={
        "accessModes": ["ReadWriteOnce"],
        "storageClassName": "local-path",
        "resources": {"requests": {"storage": "20Gi"}},
    },
    opts=k8s_opts([namespace]),
)

registry_deploy = k8s.apps.v1.Deployment(
    "registry",
    metadata=meta("registry"),
    spec={
        "replicas": 1,
        "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app": "registry"}},
        "template": {
            "metadata": {"labels": {"app": "registry"}},
            "spec": {
                "containers": [
                    {
                        "name": "registry",
                        "image": "registry:2",
                        "ports": [{"containerPort": 5000}],
                        "volumeMounts": [{"name": "data", "mountPath": "/var/lib/registry"}],
                        "readinessProbe": {"tcpSocket": {"port": 5000}, "periodSeconds": 5},
                        "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"memory": "512Mi"}},
                    }
                ],
                "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "registry-data"}}],
            },
        },
    },
    opts=k8s_opts([registry_pvc]),
)

registry_svc = k8s.core.v1.Service(
    "registry",
    metadata=meta("registry"),
    spec={
        "type": "NodePort",
        "selector": {"app": "registry"},
        "ports": [{"port": 5000, "targetPort": 5000, "nodePort": registry_port}],
    },
    opts=k8s_opts([registry_deploy]),
)


# --- 3. build + push the engine image -------------------------------------
image = docker.Image(
    "ingestion",
    build=docker.DockerBuildArgs(
        context="../ingestion",
        dockerfile="../ingestion/Dockerfile",
        platform="linux/amd64",
    ),
    image_name=f"{registry_ref}/ingestion:{image_tag}",
    skip_push=False,
    opts=pulumi.ResourceOptions(depends_on=[registry_svc, node_trust]),
)
app_image = image.repo_digest  # immutable digest ref → pods roll on rebuild


# --- secrets --------------------------------------------------------------
db_secret = k8s.core.v1.Secret(
    "db-secret",
    metadata=meta("db-secret"),
    string_data={
        "POSTGRES_PASSWORD": pg_password,
        "NEO4J_AUTH": pulumi.Output.concat("neo4j/", neo4j_password),
    },
    opts=k8s_opts([namespace]),
)

# Injected wholesale into api/worker/migrate via envFrom — keys are exactly the
# env vars the app reads (see ingestion/config.py, db.py, neo4j_client.py).
app_secret = k8s.core.v1.Secret(
    "app-secret",
    metadata=meta("app-secret"),
    string_data={
        "INGESTION_POSTGRES_URL": pulumi.Output.concat(
            "postgresql://ingestion:", pg_password, "@postgres:5432/ingestion"
        ),
        "INGESTION_NEO4J_URI": "bolt://neo4j:7687",
        "INGESTION_NEO4J_USER": "neo4j",
        "INGESTION_NEO4J_PASSWORD": neo4j_password,
        "INGESTION_OPENROUTER_API_KEY": openrouter_key,
    },
    opts=k8s_opts([namespace]),
)

cf_secret = k8s.core.v1.Secret(
    "cloudflared",
    metadata=meta("cloudflared"),
    string_data={"TUNNEL_TOKEN": tunnel_token},
    opts=k8s_opts([namespace]),
)


# --- 4. databases ---------------------------------------------------------
def db(name: str, image_ref: str, port: int, mount: str, env: list, probe: dict, storage="10Gi", mem="1Gi"):
    pvc = k8s.core.v1.PersistentVolumeClaim(
        f"{name}-data",
        metadata=meta(f"{name}-data"),
        spec={
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": "local-path",
            "resources": {"requests": {"storage": storage}},
        },
        opts=k8s_opts([namespace]),
    )
    deploy = k8s.apps.v1.Deployment(
        name,
        metadata=meta(name),
        spec={
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {
                    "containers": [
                        {
                            "name": name,
                            "image": image_ref,
                            "ports": [{"containerPort": port}],
                            "env": env,
                            "volumeMounts": [{"name": "data", "mountPath": mount}],
                            "readinessProbe": probe,
                            "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"memory": mem}},
                        }
                    ],
                    "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": f"{name}-data"}}],
                },
            },
        },
        opts=k8s_opts([pvc, db_secret]),
    )
    svc = k8s.core.v1.Service(
        name,
        metadata=meta(name),
        spec={"selector": {"app": name}, "ports": [{"port": port, "targetPort": port}]},
        opts=k8s_opts([deploy]),
    )
    return deploy, svc


postgres_deploy, postgres_svc = db(
    "postgres",
    "postgres:16",
    5432,
    "/var/lib/postgresql/data",
    env=[
        {"name": "POSTGRES_USER", "value": "ingestion"},
        {"name": "POSTGRES_DB", "value": "ingestion"},
        {"name": "PGDATA", "value": "/var/lib/postgresql/data/pgdata"},
        {"name": "POSTGRES_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "db-secret", "key": "POSTGRES_PASSWORD"}}},
    ],
    probe={"exec": {"command": ["pg_isready", "-U", "ingestion"]}, "initialDelaySeconds": 5, "periodSeconds": 5},
)

neo4j_deploy, neo4j_svc = db(
    "neo4j",
    "neo4j:5",
    7687,
    "/data",
    env=[
        {"name": "NEO4J_AUTH", "valueFrom": {"secretKeyRef": {"name": "db-secret", "key": "NEO4J_AUTH"}}},
        # Keep memory inside the 2Gi limit (Neo4j 5 otherwise auto-sizes to the node).
        {"name": "NEO4J_server_memory_heap_max__size", "value": "1G"},
        {"name": "NEO4J_server_memory_pagecache_size", "value": "512M"},
    ],
    probe={"tcpSocket": {"port": 7687}, "initialDelaySeconds": 20, "periodSeconds": 10, "failureThreshold": 12},
    mem="2Gi",
)


# --- 5. migration job (gates api/worker) ----------------------------------
migrate = k8s.batch.v1.Job(
    "migrate",
    metadata=meta("migrate"),
    spec={
        "backoffLimit": 5,
        "template": {
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "migrate",
                        "image": app_image,
                        "command": ["alembic", "upgrade", "head"],
                        "envFrom": [{"secretRef": {"name": "app-secret"}}],
                    }
                ],
            }
        },
    },
    opts=k8s_opts([postgres_deploy, app_secret, image]),
)


# --- 6. api + worker ------------------------------------------------------
def engine(name: str, cmd: list, container_extra: dict | None = None):
    container = {
        "name": name,
        "image": app_image,
        "command": cmd,
        "envFrom": [{"secretRef": {"name": "app-secret"}}],
        "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"memory": "1Gi"}},
        **(container_extra or {}),
    }
    return k8s.apps.v1.Deployment(
        name,
        metadata=meta(name),
        spec={
            "replicas": 1,
            "selector": {"matchLabels": {"app": name}},
            "template": {"metadata": {"labels": {"app": name}}, "spec": {"containers": [container]}},
        },
        opts=k8s_opts([migrate, neo4j_deploy, image]),
    )


api_deploy = engine(
    "ingestion-api",
    ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
    {
        "ports": [{"containerPort": 8000}],
        "readinessProbe": {"tcpSocket": {"port": 8000}, "initialDelaySeconds": 5, "periodSeconds": 10},
        "livenessProbe": {"tcpSocket": {"port": 8000}, "initialDelaySeconds": 30, "periodSeconds": 20},
    },
)

worker_deploy = engine("ingestion-worker", ["python", "worker.py"])

api_svc = k8s.core.v1.Service(
    "ingestion-api",
    metadata=meta("ingestion-api"),
    spec={"selector": {"app": "ingestion-api"}, "ports": [{"port": 80, "targetPort": 8000}]},
    opts=k8s_opts([api_deploy]),
)


# --- 7. cloudflare tunnel -------------------------------------------------
# In the Cloudflare dashboard, route your public hostname to:
#   http://ingestion-api.ingestion.svc.cluster.local:80
cloudflared = k8s.apps.v1.Deployment(
    "cloudflared",
    metadata=meta("cloudflared"),
    spec={
        "replicas": 1,
        "selector": {"matchLabels": {"app": "cloudflared"}},
        "template": {
            "metadata": {"labels": {"app": "cloudflared"}},
            "spec": {
                "containers": [
                    {
                        "name": "cloudflared",
                        "image": "cloudflare/cloudflared:latest",
                        "args": ["tunnel", "--no-autoupdate", "run"],
                        "env": [
                            {
                                "name": "TUNNEL_TOKEN",
                                "valueFrom": {"secretKeyRef": {"name": "cloudflared", "key": "TUNNEL_TOKEN"}},
                            }
                        ],
                        "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"memory": "256Mi"}},
                    }
                ]
            },
        },
    },
    opts=k8s_opts([cf_secret, api_svc]),
)


# --- outputs --------------------------------------------------------------
pulumi.export("image", app_image)
pulumi.export("registry", registry_ref)
pulumi.export("api_service", "ingestion-api.ingestion.svc.cluster.local:80")
pulumi.export("seed_command", "kubectl -n ingestion exec deploy/ingestion-api -- python seed.py")
