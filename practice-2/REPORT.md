# HTTP/1.1 vs HTTP/2 vs HTTP/3 — Benchmark Report

**Endpoint:** `GET /checklist` · **Load:** 500 req/s constant arrival rate, 60 s · **Stack:** FastAPI + Uvicorn behind Caddy

| Protocol | Connection | p50 | p95 | p99 | Achieved RPS | Mechanism that explains it |
|---|---|---|---|---|---|---|
| HTTP/1.1 | reused | 1.14 ms | 1.61 ms | 1.95 ms | 499.996 | baseline |
| HTTP/1.1 | new per request | 0.847 ms | 1.64 ms | 2.04 ms | 499.524 | connection churn → 5.23 % resets |
| HTTP/2 | reused | 1.26 ms | 1.63 ms | 1.95 ms | 499.986 | multiplexing, HPACK — no room to help |
| HTTP/3 | reused | — | — | — | — | no client-side h3 load generation available |
| HTTP/1.1 | netem 100 ms / 1 % | 201.31 ms | 201.86 ms | **714.39 ms** | 495.328 | loss spread across several TCP connections |
| HTTP/2 | netem 100 ms / 1 % | 201.36 ms | 201.91 ms | **804.49 ms** | 495.227 | TCP head-of-line blocking on one connection |

> **Limitation, stated up front:** HTTP/3 was enabled and verified on the server, but no HTTP/3 load-generation client was available in this environment. No h3 latency or RPS values are reported, and none were synthesised. Details in [§2](#http3).

---

## 1. Test setup

### Backend

| | |
|---|---|
| Framework | FastAPI |
| ASGI server | Uvicorn |
| Reverse proxy | Caddy (terminates TLS, proxies to port 9000) |
| Endpoint | `GET /checklist` |

```json
{
  "id": 1,
  "student": "Alikhan Aliaskar"
}
```

### Load generator

k6, constant arrival rate:

| Parameter | Value |
|---|---|
| Target rate | 500 req/s |
| Duration | 60 s |
| Pre-allocated VUs | 200 |
| Maximum VUs | 800 |
| TLS verification | disabled (Caddy internal certificate) |
| Connection reuse | enabled unless stated otherwise |

The same endpoint, arrival rate and duration were used for every HTTP/1.1 and HTTP/2 measurement.

---

## 2. Protocol verification

### HTTP/1.1

Exposed through a dedicated Caddy listener on port 8441:

```bash
curl -vk --http1.1 https://localhost:8441/checklist
```

```
ALPN: server accepted http/1.1
using HTTP/1.x
HTTP/1.1 200 OK
```

### HTTP/2

Tested through the Caddy listener on port 8442:

```bash
curl -vk --http2 https://localhost:8442/checklist
```

The negotiated connection used HTTP/2.

### HTTP/3 <a id="http3"></a>

Caddy enabled the HTTP/3 listener, and UDP 443 was published by Docker Compose:

```
enabling HTTP/3 listener addr=:443
```

Client side, however:

- the `curl`/`libcurl` builds available in the test environment carry no HTTP/3 support — the `curlimages/curl` image reports `HTTP2` but no `HTTP3` feature;
- stock k6 does not generate HTTP/3 load equivalent to the HTTP/1.1 and HTTP/2 runs.

**HTTP/3 latency and RPS are therefore not reported. Synthetic values were not substituted for missing measurements.**

---

## 3. Normal network benchmark

| Protocol | p50 | p95 | p99 | Achieved RPS | Failed |
|---|---|---|---|---|---|
| HTTP/1.1 | 1.14 ms | 1.61 ms | 1.95 ms | 499.996 | 0 % |
| HTTP/2 | 1.26 ms | 1.63 ms | 1.95 ms | 499.986 | 0 % |
| HTTP/3 | — | — | — | — | — |

Under local low-latency conditions the two protocols are indistinguishable, and both sustain the requested 500 RPS. The response is tiny and the RTT is near zero, so HTTP/2 multiplexing and HPACK header compression have no overhead left to remove — there is nothing for them to win back.

HTTP/2 should not be read as *slower* from the 0.12 ms p50 gap: p99 was identical at 1.95 ms for both.

---

## 4. The cost of the handshake

The HTTP/1.1 run was repeated with connection reuse turned off — one variable changed, same script:

```js
noConnectionReuse: __ENV.COLD === '1'
```

```bash
COLD=1 URL=https://localhost:8441 k6 run k6-load.js
```

### Cold-connection result

| Metric | Result |
|---|---|
| p50 request duration | 0.847 ms |
| p95 request duration | 1.64 ms |
| p99 request duration | 2.04 ms |
| Achieved RPS | 499.524 |
| Failed requests | **5.23 %** (1569 / 30000) |

Failures were reported as `read: connection reset by peer`.

Disabling reuse means a TCP connection plus a TLS handshake for every single request. At 500 req/s that churn was enough for the local environment to start resetting connections part-way through the 60-second run.

**Which metric the handshake cost comes from:** not `http_req_duration`. k6 accounts for connection establishment separately (`http_req_connecting`, `http_req_tls_handshaking`), so the request-duration figures above are *not* a handshake measurement, and no handshake latency in milliseconds is claimed from them. What this run does measure is the operational cost of no reuse: a 5.23 % failure rate against 0 % with reuse.

Reuse also keeps congestion-control state on an established connection instead of restarting every request from the initial congestion window.

---

## 5. The same, on a bad network

Impairment applied with Linux `tc netem` on loopback:

```bash
sudo tc qdisc add dev lo root netem delay 100ms loss 1%
```

Loopback is the right interface because the traffic never leaves it:

```bash
ip route get 127.0.0.1
# local 127.0.0.1 dev lo
```

Same 500 req/s, same 60 s.

| Protocol | p50 | p95 | p99 | Achieved RPS | Failed | Dropped iterations |
|---|---|---|---|---|---|---|
| HTTP/1.1 | 201.31 ms | 201.86 ms | 714.39 ms | 495.328 | 0 % | 58 |
| HTTP/2 | 201.36 ms | 201.91 ms | 804.49 ms | 495.227 | 0 % | 59 |
| HTTP/3 | — | — | — | — | — | — |

Median latency lands on ~201 ms for both — that is the injected 100 ms delay paid twice, once per direction. Both still hold ~495 RPS with no HTTP-level failures.

**The degradation is entirely in the tail.** p99 moves from 1.95 ms to 714.39 ms (HTTP/1.1) and 804.49 ms (HTTP/2) — a factor of ~370×.

### Why

HTTP/2 multiplexes many streams over **one** TCP connection. That removes HTTP/1.1's application-level request serialisation, but it concentrates the risk: when TCP waits for a lost segment to be retransmitted, every stream sharing that connection waits with it. This is transport-level head-of-line blocking, and HTTP/2 cannot see past it — it sits above TCP.

HTTP/1.1 suffers the same TCP loss, but k6 spreads the load over several connections. A loss on one connection stalls only the requests on that connection; the others keep moving. With 1 % loss, that spreading is worth roughly 90 ms of p99.

HPACK cuts HTTP/2 header bytes, but header compression does nothing about a retransmission timer. Fewer bytes on the wire is not the bottleneck here — waiting for a lost packet is.

The ~90 ms p99 gap is reported as an observation from these runs, not as a general claim that HTTP/1.1 beats HTTP/2 under loss.

---

## 6. HTTP/3 and head-of-line blocking — what was expected

HTTP/3 carries HTTP semantics over QUIC instead of TCP. QUIC streams are independent at the transport layer, so a lost packet blocks only the stream it belonged to — the cross-stream stalling measured in §5 for HTTP/2 does not have the same mechanism to occur through. QUIC also folds transport establishment into TLS 1.3, changing the handshake round-trip structure versus TCP-then-TLS, and uses QPACK rather than HPACK for headers.

Those are reasons to expect different behaviour on a lossy, high-latency link. **No quantitative HTTP/3 result is claimed here**, because the available load-generation clients did not support HTTP/3.

---

## 7. Summary

- **Local network:** HTTP/1.1 and HTTP/2 both sustained 500 RPS at 1.95 ms p99, zero failures. No protocol advantage is measurable when RTT and payload are both near zero.
- **No connection reuse:** 5.23 % of requests failed with connection resets at the same arrival rate. The cost of dropping reuse showed up as failures, not as latency.
- **100 ms delay + 1 % loss:** median ~201 ms for both, ~495 RPS held, but p99 blew out to 714.39 ms (HTTP/1.1) and 804.49 ms (HTTP/2). HTTP/2 multiplexing and HPACK cut application-layer overhead, but HTTP/2 still runs on TCP and still inherits TCP head-of-line blocking under loss.
- **HTTP/3:** enabled and verified server-side, not measurable client-side in this environment. No values were fabricated.
