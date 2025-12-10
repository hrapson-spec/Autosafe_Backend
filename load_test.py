#!/usr/bin/env python3
"""
AutoSafe Performance Load Testing Script
Measures response times, throughput, and error rates under various load conditions.
"""
import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Dict, List

import httpx


@dataclass
class EndpointResult:
    """Results for a single endpoint test."""
    endpoint: str
    total_requests: int
    successful: int
    failed: int
    error_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_min_ms: float
    latency_max_ms: float
    requests_per_second: float
    total_duration_s: float


@dataclass
class LoadTestReport:
    """Complete load test report."""
    timestamp: str
    base_url: str
    concurrency: int
    requests_per_endpoint: int
    endpoints: List[EndpointResult]
    summary: Dict[str, str]


async def make_request(client: httpx.AsyncClient, url: str) -> tuple[float, bool, str]:
    """Make a single request and return (latency_ms, success, error_message)."""
    start = time.perf_counter()
    try:
        response = await client.get(url, timeout=30.0)
        latency = (time.perf_counter() - start) * 1000  # Convert to ms
        success = response.status_code == 200
        error = "" if success else f"HTTP {response.status_code}"
        return latency, success, error
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        return latency, False, str(e)


async def run_concurrent_requests(
    client: httpx.AsyncClient, 
    url: str, 
    num_requests: int,
    concurrency: int
) -> List[tuple[float, bool, str]]:
    """Run requests with specified concurrency level."""
    semaphore = asyncio.Semaphore(concurrency)
    
    async def limited_request():
        async with semaphore:
            return await make_request(client, url)
    
    tasks = [limited_request() for _ in range(num_requests)]
    return await asyncio.gather(*tasks)


def calculate_percentile(data: List[float], percentile: float) -> float:
    """Calculate percentile of a list of values."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    index = int(len(sorted_data) * percentile / 100)
    return sorted_data[min(index, len(sorted_data) - 1)]


def analyze_results(
    endpoint: str, 
    results: List[tuple[float, bool, str]], 
    duration: float
) -> EndpointResult:
    """Analyze raw results into structured metrics."""
    latencies = [r[0] for r in results]
    successes = sum(1 for r in results if r[1])
    failures = len(results) - successes
    
    return EndpointResult(
        endpoint=endpoint,
        total_requests=len(results),
        successful=successes,
        failed=failures,
        error_rate=round(failures / len(results) * 100, 2) if results else 0,
        latency_p50_ms=round(calculate_percentile(latencies, 50), 2),
        latency_p95_ms=round(calculate_percentile(latencies, 95), 2),
        latency_p99_ms=round(calculate_percentile(latencies, 99), 2),
        latency_min_ms=round(min(latencies), 2) if latencies else 0,
        latency_max_ms=round(max(latencies), 2) if latencies else 0,
        requests_per_second=round(len(results) / duration, 2) if duration > 0 else 0,
        total_duration_s=round(duration, 2)
    )


async def test_endpoint(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    num_requests: int,
    concurrency: int,
    label: str
) -> EndpointResult:
    """Test a single endpoint and return results."""
    url = f"{base_url}{path}"
    print(f"\n  Testing: {label}")
    print(f"    URL: {url}")
    print(f"    Requests: {num_requests}, Concurrency: {concurrency}")
    
    start_time = time.perf_counter()
    results = await run_concurrent_requests(client, url, num_requests, concurrency)
    duration = time.perf_counter() - start_time
    
    endpoint_result = analyze_results(label, results, duration)
    
    print(f"    ✓ Completed in {endpoint_result.total_duration_s}s")
    print(f"    └─ p50: {endpoint_result.latency_p50_ms}ms, "
          f"p95: {endpoint_result.latency_p95_ms}ms, "
          f"p99: {endpoint_result.latency_p99_ms}ms")
    print(f"    └─ RPS: {endpoint_result.requests_per_second}, "
          f"Errors: {endpoint_result.error_rate}%")
    
    return endpoint_result


async def run_load_test(
    base_url: str,
    num_requests: int = 100,
    concurrency: int = 10
) -> LoadTestReport:
    """Run complete load test suite."""
    print("=" * 60)
    print("AutoSafe Performance Load Test")
    print("=" * 60)
    print(f"Target: {base_url}")
    print(f"Requests per endpoint: {num_requests}")
    print(f"Concurrency: {concurrency}")
    
    # Test endpoints
    endpoints = [
        ("/api/makes", "GET /api/makes"),
        ("/api/models?make=FORD", "GET /api/models (FORD)"),
        ("/api/risk?make=FORD&model=FIESTA&year=2018&mileage=50000", "GET /api/risk (full query)"),
    ]
    
    results = []
    
    async with httpx.AsyncClient() as client:
        # Warm-up request
        print("\n[Warm-up]")
        try:
            await client.get(f"{base_url}/health", timeout=10.0)
            print("  ✓ Server is responding")
        except Exception as e:
            print(f"  ✗ Server not responding: {e}")
            print("\nPlease ensure the server is running:")
            print("  uvicorn main:app --reload")
            return None
        
        print("\n[Running Tests]")
        for path, label in endpoints:
            result = await test_endpoint(
                client, base_url, path, num_requests, concurrency, label
            )
            results.append(result)
            await asyncio.sleep(0.5)  # Brief pause between endpoints
    
    # Generate summary
    avg_p95 = statistics.mean(r.latency_p95_ms for r in results)
    total_errors = sum(r.failed for r in results)
    total_requests = sum(r.total_requests for r in results)
    
    summary = {
        "average_p95_latency": f"{avg_p95:.2f}ms",
        "total_requests": str(total_requests),
        "total_errors": str(total_errors),
        "overall_error_rate": f"{(total_errors / total_requests * 100):.2f}%",
        "bottleneck_endpoint": max(results, key=lambda r: r.latency_p95_ms).endpoint
    }
    
    report = LoadTestReport(
        timestamp=datetime.now().isoformat(),
        base_url=base_url,
        concurrency=concurrency,
        requests_per_endpoint=num_requests,
        endpoints=[asdict(r) for r in results],
        summary=summary
    )
    
    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Average p95 Latency: {summary['average_p95_latency']}")
    print(f"  Total Requests: {summary['total_requests']}")
    print(f"  Total Errors: {summary['total_errors']}")
    print(f"  Overall Error Rate: {summary['overall_error_rate']}")
    print(f"  Slowest Endpoint: {summary['bottleneck_endpoint']}")
    
    return report


def main():
    parser = argparse.ArgumentParser(description="AutoSafe Performance Load Test")
    parser.add_argument(
        "--target", 
        default="http://localhost:8000",
        help="Base URL of the server (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=100,
        help="Number of requests per endpoint (default: 100)"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Number of concurrent requests (default: 10)"
    )
    parser.add_argument(
        "--output",
        default="load_test_results.json",
        help="Output file for JSON results (default: load_test_results.json)"
    )
    
    args = parser.parse_args()
    
    report = asyncio.run(run_load_test(
        base_url=args.target,
        num_requests=args.requests,
        concurrency=args.concurrency
    ))
    
    if report:
        # Save results to JSON
        with open(args.output, 'w') as f:
            json.dump(asdict(report), f, indent=2)
        print(f"\n✓ Results saved to {args.output}")


if __name__ == "__main__":
    main()
