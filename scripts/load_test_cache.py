"""Load test for the GET /incidents/{id} read-through cache.

Simulates a realistic usage pattern -- a small set of "active" incidents
getting looked up repeatedly (e.g. multiple on-call engineers checking
the same incidents, or a dashboard polling) -- and reports the real
cache hit rate, measured from IncidentRelay's own X-Cache response
header. Also cross-checks against the stub upstream's own per-incident
hit counter (GET /_counts) as an independent verification that the
reported hit rate matches how many requests actually reached upstream.

Usage:
    python scripts/load_test_cache.py \
        --base-url http://localhost:8000 \
        --upstream-url http://localhost:9000 \
        --incident-ids 1,2,3,4,5 \
        --duration 90 \
        --interval 0.5
"""
import argparse
import csv
import random
import time
from pathlib import Path

import httpx


def run(base_url: str, upstream_url: str, incident_ids: list[int], duration: float, interval: float, out_path: Path):
    client = httpx.Client(timeout=5.0)
    rows = []
    start = time.monotonic()
    request_count = 0

    print(f"Running for {duration:.0f}s, one request every {interval}s, "
          f"picking randomly from incident IDs {incident_ids}...\n")

    while time.monotonic() - start < duration:
        incident_id = random.choice(incident_ids)
        t0 = time.monotonic()
        try:
            response = client.get(f"{base_url}/incidents/{incident_id}")
            latency_ms = (time.monotonic() - t0) * 1000
            x_cache = response.headers.get("X-Cache", "N/A")
            status = response.status_code
        except httpx.RequestError as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            x_cache = "ERROR"
            status = 0
            print(f"  request error: {exc}")

        request_count += 1
        rows.append(
            {
                "n": request_count,
                "elapsed_s": round(time.monotonic() - start, 2),
                "incident_id": incident_id,
                "status": status,
                "x_cache": x_cache,
                "latency_ms": round(latency_ms, 2),
            }
        )
        if request_count % 10 == 0:
            print(f"  ...{request_count} requests sent")

        time.sleep(interval)

    client.close()

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    hits = sum(1 for r in rows if r["x_cache"] == "HIT")
    misses = sum(1 for r in rows if r["x_cache"] == "MISS")
    errors = sum(1 for r in rows if r["x_cache"] in ("ERROR",) or r["status"] >= 400)
    total = len(rows)
    hit_rate = hits / (hits + misses) * 100 if (hits + misses) else 0.0

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Total requests:     {total}")
    print(f"Cache hits:         {hits}")
    print(f"Cache misses:       {misses}")
    print(f"Errors/non-2xx:     {errors}")
    print(f"Cache hit rate:     {hit_rate:.1f}%")
    print(f"Raw log saved to:   {out_path}")

    per_id = {}
    for r in rows:
        d = per_id.setdefault(r["incident_id"], {"hits": 0, "misses": 0})
        if r["x_cache"] == "HIT":
            d["hits"] += 1
        elif r["x_cache"] == "MISS":
            d["misses"] += 1
    print("\nPer-incident breakdown:")
    for iid in sorted(per_id):
        d = per_id[iid]
        print(f"  incident {iid}: {d['hits']} hits, {d['misses']} misses")

    # Cross-check against the stub upstream's own counters: total misses
    # reported by IncidentRelay should equal total requests the upstream
    # actually received.
    try:
        upstream_counts = httpx.get(f"{upstream_url}/_counts", timeout=5.0).json()
        upstream_total = sum(upstream_counts.values())
        print(f"\nUpstream cross-check: stub received {upstream_total} real requests "
              f"(IncidentRelay reported {misses} cache misses) "
              f"-> {'MATCH' if upstream_total == misses else 'MISMATCH'}")
    except httpx.RequestError:
        print("\nUpstream cross-check: could not reach stub /_counts (skipped)")

    return hit_rate


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--upstream-url", default="http://localhost:9000")
    parser.add_argument("--incident-ids", default="1,2,3,4,5")
    parser.add_argument("--duration", type=float, default=90.0, help="seconds")
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between requests")
    parser.add_argument("--out", default="load_test_results.csv")
    args = parser.parse_args()

    ids = [int(x) for x in args.incident_ids.split(",")]
    run(args.base_url, args.upstream_url, ids, args.duration, args.interval, Path(args.out))
