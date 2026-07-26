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
resend_api_key = cfg.get_secret("resendApiKey")  # optional — mailer logs instead of sending if unset
auth_email_from = cfg.get("authEmailFrom") or "noreply@mail.sinpi.software"
app_base_url = cfg.get("appBaseUrl") or "https://desk.sinpi.software"
app_origins = cfg.get("appOrigins") or "https://desk.sinpi.software"


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
        # user-auth / email (mailer.py, accounts.py)
        "RESEND_API_KEY": resend_api_key if resend_api_key is not None else "",
        "AUTH_EMAIL_FROM": auth_email_from,
        "APP_BASE_URL": app_base_url,
        "APP_ORIGINS": app_origins,
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


# --- prefect ---------------------------------------------------------------
# Prefect gets its own database inside the existing postgres pod (home lab: one
# server, several databases). Idempotent: `pulumi up` re-runs this Job, so the
# CREATE is guarded by a catalog check rather than relying on IF NOT EXISTS,
# which CREATE DATABASE does not support.
prefect_db_init = k8s.batch.v1.Job(
    "prefect-db-init",
    metadata=meta("prefect-db-init"),
    spec={
        "backoffLimit": 5,
        "template": {
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "createdb",
                        "image": "postgres:16",
                        "command": ["sh", "-c"],
                        "args": [
                            'psql -h postgres -U ingestion -tc "SELECT 1 FROM pg_database WHERE datname=\'prefect\'" '
                            '| grep -q 1 || createdb -h postgres -U ingestion prefect'
                        ],
                        "env": [
                            {
                                "name": "PGPASSWORD",
                                "valueFrom": {"secretKeyRef": {"name": "db-secret", "key": "POSTGRES_PASSWORD"}},
                            }
                        ],
                    }
                ],
            }
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[postgres_deploy, db_secret]),
)

# The LAN address the browser-side UI must call. Prefect's UI runs in the browser,
# so a cluster-internal name (http://prefect:4200/api) would resolve inside the
# cluster and fail from a laptop.
prefect_lan_url = cfg.get("prefectLanUrl") or "http://192.168.0.202:4200"

prefect_secret = k8s.core.v1.Secret(
    "prefect-secret",
    metadata=meta("prefect-secret"),
    string_data={
        # Prefect 3 requires the asyncpg driver; plain postgresql:// fails at startup.
        "PREFECT_API_DATABASE_CONNECTION_URL": pulumi.Output.concat(
            "postgresql+asyncpg://ingestion:", pg_password, "@postgres:5432/prefect"
        ),
    },
    opts=ns_opts,
)

prefect_deploy = k8s.apps.v1.Deployment(
    "prefect",
    metadata=meta("prefect"),
    spec={
        "replicas": 1,
        # Singleton over one database — never two servers at once.
        "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app": "prefect"}},
        "template": {
            "metadata": {"labels": {"app": "prefect"}},
            "spec": {
                "containers": [
                    {
                        "name": "prefect",
                        "image": "prefecthq/prefect:3-python3.12",
                        "command": ["prefect", "server", "start", "--host", "0.0.0.0"],
                        "ports": [{"containerPort": 4200}],
                        "env": [
                            {"name": "PREFECT_SERVER_API_HOST", "value": "0.0.0.0"},
                            {"name": "PREFECT_API_URL", "value": pulumi.Output.concat(prefect_lan_url, "/api")},
                            {"name": "PREFECT_UI_API_URL", "value": pulumi.Output.concat(prefect_lan_url, "/api")},
                            {
                                "name": "PREFECT_API_DATABASE_CONNECTION_URL",
                                "valueFrom": {
                                    "secretKeyRef": {
                                        "name": "prefect-secret",
                                        "key": "PREFECT_API_DATABASE_CONNECTION_URL",
                                    }
                                },
                            },
                        ],
                        "resources": {"requests": {"cpu": "100m", "memory": "512Mi"}, "limits": {"memory": "2Gi"}},
                        "readinessProbe": {
                            "httpGet": {"path": "/api/health", "port": 4200},
                            "initialDelaySeconds": 15,
                            "periodSeconds": 10,
                        },
                    }
                ],
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[prefect_db_init, prefect_secret]),
)

# LoadBalancer so k3s ServiceLB binds :4200 on the node — LAN only. Prefect has no
# auth of its own, so it must NOT be exposed via the tunnel or a Traefik ingress.
prefect_svc = k8s.core.v1.Service(
    "prefect",
    metadata=meta("prefect"),
    spec={
        "type": "LoadBalancer",
        "selector": {"app": "prefect"},
        "ports": [{"port": 4200, "targetPort": 4200}],
    },
    opts=pulumi.ResourceOptions(depends_on=[prefect_deploy]),
)

pulumi.export("prefect_ui", prefect_lan_url)


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


# --- neonews (the automated newsroom; an external consumer of the engine API) ---
neonews_image = cfg.require("neonewsImage")  # e.g. localhost:5000/anything-neonews:<tag>
# Which knowledge base neonews reads and writes is decided by WHICH key you set here.
neonews_engine_api_key = cfg.require_secret("neonewsEngineApiKey")

# Keys are exactly the env var names neonews/config.py reads.
neonews_secret = k8s.core.v1.Secret(
    "neonews-secret",
    metadata=meta("neonews-secret"),
    string_data={
        "NEONEWS_POSTGRES_URL": pulumi.Output.concat(
            "postgresql://ingestion:", pg_password, "@postgres:5432/ingestion"
        ),
        "NEONEWS_ENGINE_URL": "http://ingestion-api",
        "NEONEWS_ENGINE_API_KEY": neonews_engine_api_key,
        "NEONEWS_OPENROUTER_API_KEY": openrouter_key,
        "PREFECT_API_URL": "http://prefect:4200/api",
    },
    opts=ns_opts,
)

# neonews owns its own Alembic chain (version_table alembic_version_neonews) in the
# same database, so this cannot collide with the engine's migrate Job.
neonews_migrate = k8s.batch.v1.Job(
    "neonews-migrate",
    metadata=meta("neonews-migrate"),
    spec={
        "backoffLimit": 5,
        "template": {
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "migrate",
                        "image": neonews_image,
                        "imagePullPolicy": "Always",
                        "command": ["alembic", "upgrade", "head"],
                        "envFrom": [{"secretRef": {"name": "neonews-secret"}}],
                    }
                ],
            }
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[postgres_deploy, neonews_secret]),
)

# serve.py registers the four flow deployments and executes the runs they schedule —
# no work pool or custom worker image needed. Gated on the migration so neonews can
# never start against an unmigrated schema, and on Prefect so registration has an API
# to talk to. If Prefect is unreachable the pod crashloops, which is the honest failure.
neonews_serve = k8s.apps.v1.Deployment(
    "neonews-serve",
    metadata=meta("neonews-serve"),
    spec={
        "replicas": 1,
        # Exactly one process may register these deployments; two would reconcile
        # the same schedules against each other.
        "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app": "neonews-serve"}},
        "template": {
            "metadata": {"labels": {"app": "neonews-serve"}},
            "spec": {
                "containers": [
                    {
                        "name": "neonews-serve",
                        "image": neonews_image,
                        "imagePullPolicy": "Always",
                        "command": ["python", "serve.py"],
                        "envFrom": [{"secretRef": {"name": "neonews-secret"}}],
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "256Mi"},
                            "limits": {"memory": "1Gi"},
                        },
                    }
                ],
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[neonews_migrate, prefect_deploy, api_svc]),
)


# --- web frontend (React Router 8 SSR app; Node server) ---
web_image = cfg.require("webImage")  # e.g. localhost:5000/anything-web:<tag>
web_deploy = k8s.apps.v1.Deployment(
    "web",
    metadata=meta("web"),
    spec={
        "replicas": 1,
        "selector": {"matchLabels": {"app": "web"}},
        "template": {
            "metadata": {"labels": {"app": "web"}},
            "spec": {
                "containers": [
                    {
                        "name": "web",
                        "image": web_image,
                        "imagePullPolicy": "Always",
                        "ports": [{"containerPort": 3000}],
                        "env": [
                            {"name": "PORT", "value": "3000"},
                            # SSR loaders call the engine API in-cluster (browser calls go via /api).
                            {"name": "INTERNAL_API_URL", "value": "http://ingestion-api.ingestion.svc.cluster.local:80"},
                        ],
                        "readinessProbe": {"tcpSocket": {"port": 3000}, "initialDelaySeconds": 5, "periodSeconds": 10},
                        "resources": {"requests": {"cpu": "50m", "memory": "96Mi"}, "limits": {"memory": "384Mi"}},
                    }
                ],
            },
        },
    },
    opts=ns_opts,
)
web_svc = k8s.core.v1.Service(
    "web",
    metadata=meta("web"),
    spec={"selector": {"app": "web"}, "ports": [{"port": 80, "targetPort": 3000}]},
    opts=pulumi.ResourceOptions(depends_on=[web_deploy]),
)

# Traefik Ingress. The Cloudflare Tunnel forwards the hostname to Traefik (hub pattern).
# The engine API owns its own prefixes; the RR8 app (which does its own routing for
# /login, /register, /app, …) owns everything else — Traefik's longer-prefix rule sends
# /content, /api, … to the API and "/" to the web app.
api_hostname = cfg.get("apiHostname")
if api_hostname:
    api_paths = [
        {"path": p, "pathType": "Prefix", "backend": {"service": {"name": "ingestion-api", "port": {"number": 80}}}}
        for p in ("/content", "/config", "/graphql", "/docs", "/openapi.json", "/api")
    ]
    web_path = {
        "path": "/",
        "pathType": "Prefix",
        "backend": {"service": {"name": "web", "port": {"number": 80}}},
    }
    k8s.networking.v1.Ingress(
        "site",
        metadata=meta("site"),
        spec={
            "ingressClassName": "traefik",
            "rules": [{"host": api_hostname, "http": {"paths": api_paths + [web_path]}}],
        },
        opts=pulumi.ResourceOptions(depends_on=[api_svc, web_svc]),
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
