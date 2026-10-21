import argparse
import asyncio
import csv
import statistics
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from app import catalog_pb2 as pb
from app.clients import grpc_stub, rest_client
from devservers import running_servers

RESULTS = Path("benchmarks")
GET_IDS = 500

Request = Callable[[int], Awaitable[int]]


@dataclass
class Result:
    cache: str
    variant: str
    operation: str
    concurrency: int
    repeat: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    rps: float
    errors: int
    payload_bytes: int


def rest_requests(client) -> dict[str, Request]:
    async def get(i: int) -> int:
        response = await client.get(f"/api/v1/products/{i % GET_IDS + 1}")
        response.raise_for_status()
        return len(response.content)

    async def list_(i: int) -> int:
        response = await client.get("/api/v1/products", params={"category_id": 11 + i % 90, "limit": 20})
        response.raise_for_status()
        return len(response.content)

    return {"get": get, "list": list_}


def grpc_requests(stub) -> dict[str, Request]:
    async def get(i: int) -> int:
        return (await stub.GetProduct(pb.GetProductRequest(id=i % GET_IDS + 1), timeout=5)).ByteSize()

    async def list_(i: int) -> int:
        response = await stub.ListProducts(pb.ListProductsRequest(category_id=11 + i % 90, limit=20), timeout=5)
        return response.ByteSize()

    return {"get": get, "list": list_}


async def measure(request: Request, total: int, concurrency: int) -> tuple[list[float], int, float, int]:
    latencies: list[float] = []
    errors = 0
    payload = 0
    counter = iter(range(total))

    async def worker() -> None:
        nonlocal errors, payload
        for i in counter:
            start = time.perf_counter()
            try:
                payload = await request(i)
            except Exception:
                errors += 1
                continue
            latencies.append((time.perf_counter() - start) * 1000)

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return latencies, errors, time.perf_counter() - started, payload


async def run_variant(cache: str, variant: str, requests: dict[str, Request], args) -> list[Result]:
    results = []
    for operation, request in requests.items():
        for concurrency in args.concurrency:
            for repeat in range(1, args.repeats + 1):
                await measure(request, args.warmup, concurrency)
                latencies, errors, elapsed, payload = await measure(request, args.requests, concurrency)
                q = statistics.quantiles(latencies, n=100)
                result = Result(
                    cache, variant, operation, concurrency, repeat,
                    round(q[49], 3), round(q[94], 3), round(q[98], 3),
                    round(len(latencies) / elapsed, 1), errors, payload,
                )
                print(result, flush=True)
                results.append(result)
    return results


async def run_all(cache: str, args) -> list[Result]:
    results = []
    for variant, http2 in (("rest-http1.1", False), ("rest-http2", True)):
        async with rest_client(http2=http2) as client:
            results += await run_variant(cache, variant, rest_requests(client), args)
    channel, stub = grpc_stub(retries=False)
    async with channel:
        results += await run_variant(cache, "grpc", grpc_requests(stub), args)
    return results


def flush_redis() -> None:
    subprocess.run(["docker", "compose", "exec", "-T", "redis", "redis-cli", "flushall"], check=True)


def summary(results: list[Result]) -> str:
    groups: dict[tuple, list[Result]] = {}
    for r in results:
        groups.setdefault((r.cache, r.operation, r.concurrency, r.variant), []).append(r)

    lines = [
        "| cache | operation | concurrency | variant | p50 ms | p95 ms | p99 ms | RPS | errors | payload B |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (cache, operation, concurrency, variant), runs in groups.items():
        med = lambda field: statistics.median(getattr(r, field) for r in runs)
        lines.append(
            f"| {cache} | {operation} | {concurrency} | {variant} | {med('p50_ms'):.2f} | {med('p95_ms'):.2f} "
            f"| {med('p99_ms'):.2f} | {med('rps'):.0f} | {sum(r.errors for r in runs)} | {runs[0].payload_bytes} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmup", type=int, default=500)
    parser.add_argument("--requests", type=int, default=5000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 20])
    args = parser.parse_args()

    results: list[Result] = []
    for cache in ("on", "off"):
        flush_redis()
        with running_servers(cache_enabled="true" if cache == "on" else "false"):
            results += asyncio.run(run_all(cache, args))

    RESULTS.mkdir(exist_ok=True)
    with open(RESULTS / "results.csv", "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(asdict(results[0])))
        writer.writeheader()
        writer.writerows(asdict(r) for r in results)
    table = summary(results)
    (RESULTS / "results.md").write_text(table + "\n")
    print(table)


if __name__ == "__main__":
    main()
