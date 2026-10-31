# SIS #2 — Product Catalog: REST vs gRPC

The product catalog from SIS #1, served over three protocols, with idempotency, timeouts and retries. Benchmark results are in [REPORT.md](REPORT.md).

| Variant | Port | Transport | Format |
|---|---|---|---|
| REST, HTTP/1.1 (baseline) | 8443 | TLS, ALPN `http/1.1` | JSON |
| REST, HTTP/2 | 8443 (same server) | TLS, ALPN `h2` | JSON |
| gRPC | 50051 | TLS, HTTP/2 | Protobuf |

**Stack:** Python 3.12, FastAPI + Hypercorn, grpc.aio, SQLAlchemy + asyncpg, Alembic, PostgreSQL 17, Redis 7.4.

## Run

```bash
uv sync
make up migrate seed   # PostgreSQL + Redis, schema, 10k products
make certs proto       # TLS certificate, gRPC code

make rest-tls          # REST  https://localhost:8443
make grpc              # gRPC  localhost:50051
make outbox            # outbox worker

make test              # integration tests
make bench             # benchmark -> benchmarks/
```

## Architecture

Green: implemented. Yellow: mocked. Grey: SIS #1 design only.

```mermaid
flowchart TB
    client["Web / mobile clients"]
    admin["Admin, integrations"]
    lb["Load balancer"]

    subgraph api["Stateless API"]
        rest["REST: HTTP/1.1 + HTTP/2"]
        grpcs["gRPC"]
        svc["service.py"]
        rest --> svc
        grpcs --> svc
    end

    redis[("Redis cache")]
    pg[("PostgreSQL primary")]
    replica[("Read replica")]
    worker["Outbox worker"]
    search["OpenSearch"]

    client --> lb
    admin --> lb
    lb --> rest
    lb --> grpcs
    svc -->|"read: cache-aside"| redis
    svc -->|"cache miss"| replica
    svc -->|"write + outbox event"| pg
    pg -.->|"replication"| replica
    worker -->|"poll events"| pg
    worker -->|"DEL key"| redis
    worker -->|"reindex"| search

    classDef real fill:#d3f9d8,stroke:#2b8a3e,color:#000
    classDef mock fill:#fff3bf,stroke:#e67700,color:#000
    classDef design fill:#e9ecef,stroke:#868e96,color:#000,stroke-dasharray: 5 5
    class rest,grpcs,svc,redis,pg,worker real
    class replica,search mock
    class client,admin,lb design
```

- **Read replica (mock):** reads use a separate pool (`READ_DATABASE_URL`), currently the same database.
- **OpenSearch (mock):** `FakeSearchIndex` receives reindex calls. Listing is served from PostgreSQL, the SIS #1 fallback mode.
- **Not deployed:** load balancer, CDN, S3, Kubernetes.

## Read: cache-aside

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant R as Redis
    participant DB as PostgreSQL
    C->>A: GET /products/42
    A->>R: GET product:42 (10 ms timeout)
    alt hit
        A-->>C: 200
    else miss or Redis down
        A->>DB: SELECT (50 ms timeout)
        A->>R: SET product:42, TTL 60 s + jitter (404 cached 30 s)
        A-->>C: 200 / 404
    end
```

## Write: idempotency and retries

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant DB as PostgreSQL
    C->>A: POST /admin/products, Idempotency-Key: K
    A-->>C: 503
    Note over C: backoff ~100 ms, same key K
    C->>A: POST /admin/products, Idempotency-Key: K
    A->>DB: one transaction: claim key K + insert product + outbox event
    A-->>C: 201 {id: 7}
    Note over C: response lost, retry with K
    C->>A: POST /admin/products, Idempotency-Key: K
    A-->>C: 201 {id: 7}, idempotent-replayed: true
```

| | REST | gRPC |
|---|---|---|
| Idempotency key | `Idempotency-Key` header | `idempotency-key` metadata |
| Optimistic locking | `If-Match: <version>` → 412 | `expected_version` → `FAILED_PRECONDITION` |
| Retries | 3 attempts, 3 s total budget, on 502/503/504 | built-in `retryPolicy`, on `UNAVAILABLE` |
| Deadline | `X-Request-Timeout` → 504 | gRPC deadline → `DEADLINE_EXCEEDED` |
| Dependency failure | 503 + `Retry-After` | `UNAVAILABLE` |

## Project layout

```
app/
  api/          rest.py, grpc_server.py, generated gRPC code
  services/     service.py (business logic), search.py (OpenSearch mock)
  database/     db.py, models.py
  cache/        cache.py
  workers/      outbox_worker.py
  config.py  schemas.py  clients.py
proto/          catalog.proto
migrations/     Alembic
tests/          integration tests
bench.py        benchmark
```
