"""Deploy the knowledge-graph engine onto the `skynet` single-node k3s cluster.

The cluster already has a registry (docker registry:2 on the node at :5000, trusted
by containerd as localhost:5000 via registries.yaml). So this program does NOT
manage a registry or node config — the image is built+pushed out of band (see
README) and referenced here by tag. Pulumi only deploys the workloads:

  namespace + secrets → postgres + neo4j → migrate (alembic) → api + worker
  → cloudflared (only if a tunnel token is configured)

Runs against the cluster via KUBECONFIG (fetch the node's /etc/rancher/k3s/k3s.yaml).
"""

import pulumi
import pulumi_kubernetes as k8s

NS = "ingestion"
cfg = pulumi.Config()

# Full image ref as the NODE resolves it. registries.yaml maps localhost:5000 →
# the node's registry, so pods pull localhost:5000/<repo>:<tag>.
image = cfg.require("image")  # e.g. localhost:5000/anything-ingestion:<tag>
pg_password = cfg.require_secret("pgPassword")  # keep URL-safe (alphanumeric)
neo4j_password = cfg.require_secret("neo4jPassword")
openrouter_key = cfg.require_secret("openrouterApiKey")
tunnel_token = cfg.get_secret("tunnelToken")  # optional — omit to skip the tunnel


def meta(name: str) -> dict:
    return {"name": name, "namespace": NS}


namespace = k8s.core.v1.Namespace("ns", metadata={"name": NS})
ns_opts = pulumi.ResourceOptions(depends_on=[namespace])


# --- secrets --------------------------------------------------------------
db_secret = k8s.core.v1.Secret(
    "db-secret",
    metadata=meta("db-secret"),
    string_data={
        "POSTGRES_PASSWORD": pg_password,
        "NEO4J_AUTH": pulumi.Output.concat("neo4j/", neo4j_password),
    },
    opts=ns_opts,
)

# Injected wholesale into api/worker/migrate via envFrom — keys are exactly the
# env vars the app reads (ingestion/config.py, db.py, neo4j_client.py).
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
    opts=ns_opts,
)


# --- databases ------------------------------------------------------------
def db(name: str, image_ref: str, port: int, mount: str, env: list, probe: dict, storage="10Gi", mem="1Gi"):
    pvc = k8s.core.v1.PersistentVolumeClaim(
        f"{name}-data",
        metadata=meta(f"{name}-data"),
        spec={
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": "local-path",
            "resources": {"requests": {"storage": storage}},
        },
        opts=ns_opts,
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
        opts=pulumi.ResourceOptions(depends_on=[pvc, db_secret]),
    )
    svc = k8s.core.v1.Service(
        name,
        metadata=meta(name),
        spec={"selector": {"app": name}, "ports": [{"port": port, "targetPort": port}]},
        opts=pulumi.ResourceOptions(depends_on=[deploy]),
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
        {"name": "NEO4J_server_memory_heap_max__size", "value": "1G"},
        {"name": "NEO4J_server_memory_pagecache_size", "value": "512M"},
    ],
    probe={"tcpSocket": {"port": 7687}, "initialDelaySeconds": 20, "periodSeconds": 10, "failureThreshold": 12},
    mem="2Gi",
)


# --- migration job (gates api/worker) -------------------------------------
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
                        "image": image,
                        "imagePullPolicy": "Always",
                        "command": ["alembic", "upgrade", "head"],
                        "envFrom": [{"secretRef": {"name": "app-secret"}}],
                    }
                ],
            }
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[postgres_deploy, app_secret]),
)


# --- api + worker ---------------------------------------------------------
def engine(name: str, cmd: list, container_extra: dict | None = None):
    container = {
        "name": name,
        "image": image,
        "imagePullPolicy": "Always",
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
        opts=pulumi.ResourceOptions(depends_on=[migrate, neo4j_deploy]),
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
    opts=pulumi.ResourceOptions(depends_on=[api_deploy]),
)

# Optional Traefik Ingress. The Cloudflare Tunnel forwards a hostname to Traefik
# (hub pattern), so this exposes the API at that hostname: tunnel → traefik → here.
api_hostname = cfg.get("apiHostname")
if api_hostname:
    k8s.networking.v1.Ingress(
        "ingestion-api",
        metadata=meta("ingestion-api"),
        spec={
            "ingressClassName": "traefik",
            "rules": [
                {
                    "host": api_hostname,
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {"service": {"name": "ingestion-api", "port": {"number": 80}}},
                            }
                        ]
                    },
                }
            ],
        },
        opts=pulumi.ResourceOptions(depends_on=[api_svc]),
    )


# --- cloudflare tunnel (optional) -----------------------------------------
# Route your public hostname to http://ingestion-api.ingestion.svc.cluster.local:80
# in the Cloudflare dashboard, then set the tunnelToken config.
if tunnel_token is not None:
    cf_secret = k8s.core.v1.Secret(
        "cloudflared", metadata=meta("cloudflared"), string_data={"TUNNEL_TOKEN": tunnel_token}, opts=ns_opts
    )
    k8s.apps.v1.Deployment(
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
        opts=pulumi.ResourceOptions(depends_on=[cf_secret, api_svc]),
    )


pulumi.export("image", image)
pulumi.export("api_service", "ingestion-api.ingestion.svc.cluster.local:80")
pulumi.export("seed_command", "kubectl -n ingestion exec deploy/ingestion-api -- python seed.py")
