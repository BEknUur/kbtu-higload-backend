# SIS #2 — Product Catalog: gRPC and HTTP/2 vs REST/JSON

**Task:** implement the service interface using the protocol justified in SIS #1 (gRPC / HTTP-2 / WebSocket), with idempotency, timeouts and retries; measure latency and payload size against a REST/JSON baseline.

**Implementation:** Python 3.12, FastAPI + Hypercorn (REST over HTTP/1.1 and HTTP/2), grpc.aio (gRPC), PostgreSQL 17, Redis 7.4.

## Key results

Product card (`GET /products/{id}` / `GetProduct`), warm Redis cache, median of three runs of 5,000 requests each:

| Variant | p50, 1 client | p99, 20 clients | RPS, 20 clients | Response |
|---|---|---|---|---|
| REST/JSON, HTTP/1.1 (baseline) | 1.21 ms | 128.5 ms | 739 | 334 B |
| REST/JSON, HTTP/2 | 1.53 ms | **28.4 ms** | 1078 | 334 B |
| gRPC/Protobuf (HTTP/2) | **0.43 ms** | **6.6 ms** | **3769** | **172 B** |

- **Payload:** Protobuf is about half the size of JSON — a product card is 172 B vs 334 B (−48%), a page of 20 products is 3569 B vs 6805 B (−48%).
- **HTTP/2 vs HTTP/1.1:** with one client there is no gain (HTTP/2 is even slightly slower); with 20 concurrent requests the p99 tail of HTTP/2 is 4.5× shorter and RPS is 1.5× higher, thanks to multiplexing over a single connection.
- **gRPC vs REST:** card p50 is 2.8× lower and RPS at 20 clients is 5× higher than the baseline. The gain comes from the format (Protobuf), the transport (HTTP/2) and a lighter server stack — not from the protocol alone (see §8).
- **SIS #1 target (p99 < 80 ms)** at 20 concurrent requests is met by HTTP/2 and gRPC, but not by HTTP/1.1 (128–184 ms).

---

## 1. Protocol choice

| Variant | Role | Transport | Format |
|---|---|---|---|
| REST/JSON over HTTP/1.1 | baseline | TLS, ALPN `http/1.1` | JSON |
| REST/JSON over HTTP/2 | transport effect | TLS, ALPN `h2` | JSON |
| gRPC | transport + format effect | TLS, HTTP/2 | Protobuf |

The SIS #1 catalog is a read-heavy service (Read:Write = 500:1, 10k read RPS) with a public REST API. Therefore:
- **HTTP/2** keeps the REST contract for external clients (browser, mobile app, CDN) but removes the one-request-per-connection limit, which matters under many concurrent requests.
- **gRPC** is for internal clients (other services, admin panel, integrations): a strict `.proto` contract, a compact binary format, built-in deadlines and retry policy.
- **WebSocket** was not chosen: the catalog is request/response, with no real-time push events.

REST over HTTP/1.1 and HTTP/2 is served by **the same** Hypercorn process on one port — the client picks the protocol during the TLS handshake (ALPN), so the only difference is the protocol. gRPC also runs over TLS with the same certificate.

## 2. Architecture

```
REST client ─┬─ HTTP/1.1 ─┐
             └─ HTTP/2 ───┤ Hypercorn :8443 (TLS) ── app/api/rest.py ─────────┐
gRPC client ─── HTTP/2 ──── grpc.aio  :50051 (TLS) ── app/api/grpc_server.py ─┤
                                                                              ▼
                                                 app/services/service.py (shared business logic)
                                                    │                          │
                                           app/cache/cache.py          app/database/ (db.py, models.py)
                                              Redis 7.4                PostgreSQL 17 (primary + mocked replica)
                                                    ▲                          │ outbox
                                                    └── DEL ── app/workers/outbox_worker.py ── reindex ──▶ app/services/search.py (OpenSearch mock)
```

Flow diagrams are in [README.md](README.md). SIS #1 components that are not deployed (load balancer, CDN, S3, Kubernetes) are marked there separately; the read replica and OpenSearch are mocked.

| File | Responsibility |
|---|---|
| `app/database/models.py` | SQLAlchemy tables: `categories`, `products`, `idempotency_keys`, `outbox`; indexes |
| `migrations/` | Alembic migrations |
| `app/schemas.py` | Pydantic API models: `ProductOut`, `ProductCreate`, `ProductUpdate`, `Price` |
| `app/services/service.py` | catalog operations, idempotency, versioning, outbox events, cache invalidation |
| `app/database/db.py` | pools `Session` (primary, writes) and `ReadSession` (replica, reads; mocked as the same DB) |
| `app/workers/outbox_worker.py`, `app/services/search.py` | outbox worker: Redis `DEL` and reindex into the OpenSearch mock |
| `app/cache/cache.py` | Redis cache-aside, TTL with jitter, 404 caching, works when Redis is down |
| `app/api/rest.py` | REST routes, error codes, `Deadline` middleware |
| `proto/catalog.proto`, `app/api/grpc_server.py` | gRPC contract and server |
| `app/clients.py` | REST/gRPC clients with timeouts and retries |
| `tests/`, `bench.py`, `devservers.py` | tests, benchmark, server launcher for both |

Business rules are written once in `service.py`. REST and gRPC only convert inputs and errors, so they return identical data (covered by a test).

## 3. Contract

| Operation | REST | gRPC |
|---|---|---|
| Card | `GET /api/v1/products/{id}` | `GetProduct` |
| List | `GET /api/v1/products?category_id=&price_min=&price_max=&attr.color=&cursor=&limit=` | `ListProducts` |
| Categories | `GET /api/v1/categories` | `ListCategories` |
| Create | `POST /api/v1/admin/products` + `Idempotency-Key` | `CreateProduct` + `idempotency-key` metadata |
| Update | `PATCH /api/v1/admin/products/{id}` + `Idempotency-Key` + `If-Match: <version>` | `UpdateProduct` (`expected_version`, `update_mask`) |
| Delete (soft) | `DELETE /api/v1/admin/products/{id}` | `DeleteProduct` |

- **Price** is stored as `NUMERIC(12,2)`, held as `Decimal` in Python and sent as a string (`"10.50"`) in JSON and Protobuf — no float precision loss.
- **Pagination** is cursor-based (`id > cursor ORDER BY id LIMIT n`) with index `(category_id, id) WHERE status='active'`; page cost does not grow with page number, unlike `OFFSET`.
- **PATCH** changes only the fields sent (`exclude_unset`); `null` for a field → 422. In gRPC, `google.protobuf.FieldMask` lists the fields to change; a field outside the allowed set (e.g. `sku`) → `INVALID_ARGUMENT`.

| Situation | REST | gRPC |
|---|---|---|
| product missing / deleted | 404 | `NOT_FOUND` |
| stale version | 412 | `FAILED_PRECONDITION` |
| idempotency key reused for another request, invalid data | 422 | `INVALID_ARGUMENT` |
| SKU taken, unknown category | 409 | `ALREADY_EXISTS` |
| DB/Redis unavailable, pool exhausted, injected fault | 503 + `Retry-After: 1` | `UNAVAILABLE` |
| request deadline exceeded | 504 | `DEADLINE_EXCEEDED` |

## 4. Idempotency, timeouts, retries

### Idempotency (`app/services/service.py`)
- Writes (`create`, `update`) require a key: `Idempotency-Key` (REST) or `idempotency-key` (gRPC metadata).
- In **the same transaction** as the product change, `INSERT INTO idempotency_keys ... ON CONFLICT DO NOTHING` runs, and the normalized response is stored after the write. The product and the key are committed together or not at all.
- A retry with the same key and the same data → the stored response (header/trailer `idempotent-replayed: true`), with no second write and no second version bump. The same key with different data → 422 / `INVALID_ARGUMENT`.
- The request hash is built from **validated** data (operation, product id, expected version, fields), not raw bytes. `Price` normalizes `10.5` and `"10.50"` to the same value, so a retry over **the other protocol** is recognized too.
- Concurrent duplicates: the second `INSERT` of the same key waits on the primary key until the first transaction ends, then receives its result.
- DELETE is naturally idempotent: a repeat finds no active row and changes nothing (version and updated_at stay the same).

### Concurrent updates
`UPDATE ... WHERE id=? AND status='active' AND version=? ... RETURNING` checks the version and writes in one atomic statement. If 0 rows are updated, a separate query distinguishes "no such product" (404) from "stale version" (412).

### Timeouts (values from the SIS #1 latency budget)
| Level | Value | Where |
|---|---|---|
| Redis command | 10 ms | `socket_timeout` in `cache.py`; on error — cache miss, read from PostgreSQL |
| SQL query | 50 ms | asyncpg `command_timeout` |
| Waiting for a pool connection | 50 ms | SQLAlchemy `pool_timeout` |
| One client attempt | 1 s | `clients.py` |
| Whole request incl. all attempts | 3 s | client: `BUDGET`; REST server: `Deadline` (`X-Request-Timeout`); gRPC server: `context.time_remaining()` |

The server never works longer than the client is willing to wait: the REST client sends the remaining time in `X-Request-Timeout`, gRPC uses its native deadline. When the deadline expires, processing is cancelled and resources (the DB connection) are released.

### Retries (`app/clients.py`)
- **REST** — `rest_request()`: up to 3 attempts, ~100 and ~200 ms pauses with random jitter, a 3 s total budget for all attempts and pauses. Only 502/503/504 and network errors are retried; 4xx are not. Writes are retried with the same `Idempotency-Key`.
- **gRPC** — the built-in `retryPolicy` in `grpc.service_config` (3 attempts, backoff 0.1→0.2 s, `UNAVAILABLE` only). Retrying writes is safe because the idempotency-key metadata is resent with the call.
- **Retry testing** is deterministic: with `FAULT_INJECTION=true`, headers `x-fault-id` / `x-fail-times: N` make the server return 503 / `UNAVAILABLE` for exactly the first N attempts.

## 5. Tests

`make test` — 27 integration tests (pytest); they start the REST and gRPC servers themselves via `devservers.py`. All pass in ~4–7 s.

| Group | What is checked |
|---|---|
| Protocols | HTTP/2 is negotiated (`response.http_version == "HTTP/2"`); REST and gRPC return the same product |
| Reads | cursor pagination has no gaps or duplicates (pages of 7 = pages of 100); a deleted product is hidden |
| Versioning | of two concurrent PATCHes with the same version exactly one wins (200 + 412); a missing product → 404, not 412 |
| Idempotency | 10 concurrent POSTs with one key → one product; retry via gRPC after REST → same product; a repeated PATCH does not bump the version twice; a key with a different body is rejected in both protocols; "response lost, client retried" → no duplicate; a repeated DELETE does not change version/updated_at |
| Rollback | a failed write (taken SKU, 409) → the idempotency key is rolled back too and can be reused |
| Validation | PATCH with `null` → 422; `update_mask` with `sku` → `INVALID_ARGUMENT` |
| Retries | 2 faults → success (REST and gRPC); 5 faults → exactly 3 attempts and 503 / `UNAVAILABLE`; 404 is not retried; a POST retried after faults creates one product |
| Timeouts | REST server deadline → 504; the client budget bounds all attempts; gRPC deadline → `DEADLINE_EXCEEDED` |
| Cache | after GET the key is in Redis; after PATCH it is deleted and the next GET returns fresh data; 404 is cached; the catalog works when Redis is down |
| Outbox | create / update / delete write an event; the worker deletes the Redis key, indexes and removes the document in the search mock, sets `processed_at` |

## 6. Benchmark method

| Parameter | Value |
|---|---|
| Data | 10,000 products, 100 categories (`make seed`), identical on every run |
| Operations | `get` — product card (ids 1..500 round-robin); `list` — 20 products of a category (90 categories round-robin) |
| Series | 500 warm-up + 5,000 measured requests |
| Concurrency | 1 and 20 concurrent requests |
| Repeats | 3 series per combination, tables show the median |
| Cache modes | `on` (Redis filled during warm-up), `off` (`CACHE_ENABLED=false`, everything from PostgreSQL) |
| Connections | one client per variant, connections are reused |
| Disabled | retries, fault injection, compression |
| Server | 1 Hypercorn process, 1 grpc.aio process |
| Payload | response body size: JSON length and Protobuf `ByteSize()`, excluding HTTP headers and TLS |
| Machine | Intel Core i5-11400H (12 threads), 16 GB RAM, Linux 6.14; client, servers, PostgreSQL and Redis on one machine |
| Versions | FastAPI 0.157, Hypercorn 0.18, h2 4.4, httpx 0.28, grpcio 1.84, protobuf 7.36, SQLAlchemy 2.1, asyncpg 0.32 |

Run: `make bench` → `benchmarks/results.csv` (all 72 series) and `benchmarks/results.md` (medians).

## 7. Results

### 7.1 Payload size

| Response | JSON | Protobuf | Difference |
|---|---|---|---|
| Product card | 334 B | 172 B | −48% |
| Page of 20 products | 6805 B | 3569 B | −48% |

HTTP/1.1 and HTTP/2 carry the same JSON body. Protobuf saves on field names (tag numbers instead of strings like `"category_id"`), numbers (varint instead of text) and dates (`Timestamp` as two integers instead of an ISO string).

### 7.2 Latency and throughput — cache on

| Operation | Clients | Variant | p50 ms | p95 ms | p99 ms | RPS | Errors |
|---|---|---|---|---|---|---|---|
| get | 1 | REST HTTP/1.1 | 1.21 | 1.49 | 1.73 | 806 | 0 |
| get | 1 | REST HTTP/2 | 1.53 | 1.85 | 2.12 | 638 | 0 |
| get | 1 | gRPC | **0.43** | **0.56** | **0.67** | **2218** | 0 |
| get | 20 | REST HTTP/1.1 | 16.04 | 81.30 | 128.53 | 739 | 0 |
| get | 20 | REST HTTP/2 | 17.95 | 22.38 | 28.43 | 1078 | 0 |
| get | 20 | gRPC | **5.19** | **5.89** | **6.57** | **3769** | 0 |
| list | 1 | REST HTTP/1.1 | 2.70 | 2.99 | 3.37 | 367 | 0 |
| list | 1 | REST HTTP/2 | 3.34 | 4.59 | 5.49 | 288 | 0 |
| list | 1 | gRPC | **1.98** | **2.35** | **2.65** | **499** | 0 |
| list | 20 | REST HTTP/1.1 | 29.70 | 92.78 | 178.17 | 542 | 0 |
| list | 20 | REST HTTP/2 | 33.15 | 56.80 | 73.80 | 564 | 0 |
| list | 20 | gRPC | **26.41** | **27.76** | **28.70** | **755** | 0 |

### 7.3 Latency and throughput — cache off

| Operation | Clients | Variant | p50 ms | p95 ms | p99 ms | RPS | Errors |
|---|---|---|---|---|---|---|---|
| get | 1 | REST HTTP/1.1 | 1.90 | 2.17 | 2.40 | 522 | 0 |
| get | 1 | REST HTTP/2 | 2.05 | 2.31 | 2.50 | 480 | 0 |
| get | 1 | gRPC | **0.96** | **1.04** | **1.13** | **1028** | 0 |
| get | 20 | REST HTTP/1.1 | 16.04 | 107.09 | 170.13 | 601 | 0 |
| get | 20 | REST HTTP/2 | 20.74 | 25.61 | 50.30 | 941 | 0 |
| get | 20 | gRPC | **13.65** | **14.16** | **14.56** | **1459** | 0 |
| list | 1 | REST HTTP/1.1 | 2.61 | 2.90 | 3.13 | 380 | 0 |
| list | 1 | REST HTTP/2 | 2.91 | 3.20 | 3.44 | 341 | 0 |
| list | 1 | gRPC | **1.86** | **2.00** | **2.20** | **533** | 0 |
| list | 20 | REST HTTP/1.1 | 26.88 | 98.70 | 183.65 | 570 | 1 |
| list | 20 | REST HTTP/2 | 30.36 | 45.35 | 64.56 | 621 | 0 |
| list | 20 | gRPC | **24.68** | **27.35** | **32.27** | **795** | 0 |

Errors across all 72 series (396,000 requests): 1 — a 503 for HTTP/1.1 at 20 clients (see §8).

## 8. Analysis

**HTTP/1.1 → HTTP/2 (transport effect, same JSON format).**
- With **one** request at a time HTTP/2 is not faster but 0.1–0.6 ms slower: there is nothing to multiplex, and HTTP/2 framing (the `h2` library is pure Python on both client and server) costs more than plain-text HTTP/1.1. Headers are small, so HPACK savings are negligible.
- With **20** concurrent requests the medians are close, but the **tail** differs 2.5–4.5×: card p99 is 128 ms for HTTP/1.1 and 28 ms for HTTP/2. HTTP/1.1 serves 20 requests over 20 separate TCP+TLS connections, each strictly one request at a time; scheduling across connections is uneven and some requests wait long. HTTP/2 carries all 20 requests as streams of one connection. The p99 is stable across runs (HTTP/1.1, 20 clients: 125–215 ms in all 12 series), so it is a protocol property, not noise.
- Only HTTP/1.1 at 20 clients produces 503s (1 in the final run, ~0.25% in a separate check): 20 connections hit the 10-connection DB pool at once, and a request that gets no connection within 50 ms is rejected — intended load shedding, not a failure.

**REST/HTTP/2 → gRPC (format and stack effect).**
- Cached card: p50 0.43 ms vs 1.53 ms, RPS at 20 clients 3769 vs 1078. For a short request the processing cost dominates: FastAPI (routing, middleware, Pydantic `ProductOut` validation, JSON) and pure-Python `h2` vs grpc.aio, where HTTP/2 and Protobuf serialization are implemented in C++.
- For the list the gap is smaller (p50 1.98 vs 3.34 ms; at 20 clients p50 26 vs 33 ms): time goes into the SQL query, which is the same for all variants. The larger the database share of the work, the less the protocol matters.
- So the gRPC ↔ REST difference **cannot be attributed to HTTP/2 alone**: it includes the serialization format, the client libraries (grpcio vs httpx) and the server stack (grpc.aio vs Hypercorn + FastAPI).

**Cache.** Redis lowers card p50 by ~0.5–0.7 ms (REST HTTP/1.1: 1.90 → 1.21 ms; gRPC: 0.96 → 0.43 ms) and raises gRPC RPS at 20 clients from 1459 to 3769. The list is not cached; its numbers match in both modes within noise.

**SIS #1 targets.** p99 < 80 ms at 20 concurrent requests: HTTP/2 (28–74 ms) and gRPC (6.6–32 ms) — yes, HTTP/1.1 (128–184 ms) — no. The 10,000 RPS target applies to a cluster of 12–24 pods; one process on a laptop gives 740–3770 RPS for the card, while SIS #1 assumed 1000 RPS per pod.

## 9. Issues found during measurement

1. **HTTP/2 errors every 1000 requests.** The first run showed exactly 5 errors per 5,000 requests at 1 client and ~100 at 20. Cause: Hypercorn closes a connection after 1000 requests by default (`keep_alive_max_requests = 1000`), for HTTP/2 with a GOAWAY frame, and httpx drops the streams in flight at that moment. Fixed in `hypercorn.toml` (`keep_alive_max_requests = 1000000`); the second run had 0 HTTP/2 errors. In production the client retry covers such errors (`rest_request` retries network errors).
2. **504 after the response started.** If the deadline expired while the response was already being sent, `Deadline` tried to send a second response and the server logged an error. Fixed: the middleware tracks whether the response has started and, if so, just closes the stream.

## 10. Limitations

- Client, servers, PostgreSQL and Redis share one machine and its CPU; at 20 clients part of the latency is the Python client itself. The network is loopback, with no real delay or loss (the effect of loss on HTTP/1.1 vs HTTP/2 was measured in practice-2: TCP head-of-line blocking worsens the HTTP/2 tail).
- Payload size is the message body only; HTTP headers, HPACK and TLS are not counted.
- Cache invalidation: a direct `DEL` after commit (fast path) plus an outbox event in the same transaction, which the worker processes within ~0.2 s even if the API process crashed between commit and `DEL` or Redis was down. One race remains — a read that writes the old version right after `DEL`: the worker's `DEL` covers it if it runs later; otherwise the card lives until TTL expiry (~72 s). Checkout must read the price from the primary.
- The benchmark (§7) was taken before outbox and `ReadSession` were added; the read path is unchanged (a separate pool to the same DB), and writes add one `INSERT` to the transaction.
- Mocks: the read replica is a separate pool to the same DB (no replication lag); OpenSearch is `FakeSearchIndex` in the worker's memory, the product list is served from PostgreSQL (the SIS #1 fallback mode), no facets. With a single outbox worker event order is preserved; with several workers (`SKIP LOCKED`) per-product order is not guaranteed. Processed events are not deleted.
- Not covered (out of SIS #2 scope): protection against a hot-key miss stampede (single-flight), load balancer, CDN, Kubernetes, admin authorization.
- `idempotency_keys` rows are never deleted; production needs cleanup by `created_at` (e.g. older than 24 h).

## 11. Reproduce

```bash
uv sync
make up            # PostgreSQL + Redis
make migrate       # tables (Alembic)
make seed          # 100 categories, 10,000 products (make seed COUNT=1000000 for 1M)
make certs         # self-signed TLS certificate for localhost
make proto         # gRPC code from proto/catalog.proto

make rest-tls      # REST: https://localhost:8443 (HTTP/1.1 + HTTP/2)
make grpc          # gRPC: localhost:50051 (TLS)
make rest          # REST for development: http://127.0.0.1:8000, /docs
make outbox        # outbox worker

make test          # 27 integration tests (start the servers themselves)
make bench         # benchmark, ~12 minutes → benchmarks/
```

Manual protocol check:
```bash
curl -sv --http1.1 --cacert certs/cert.pem https://localhost:8443/api/v1/products/1 2>&1 | grep "< HTTP"
curl -sv --http2   --cacert certs/cert.pem https://localhost:8443/api/v1/products/1 2>&1 | grep "< HTTP"
```
