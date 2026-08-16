from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
import time

import requests

FINNHUB_BASE = "https://finnhub.io/api/v1"
ANNUAL_PS_KEYS = ("psAnnual", "priceToSalesAnnual", "priceSalesAnnual")


@dataclass(frozen=True)
class PeerBenchmark:
    status: str
    score: float | None
    candidate_ps: float | None
    peer_ps_median: float | None
    candidate_to_peer_ratio: float | None
    peer_count: int
    peer_symbols: tuple[str, ...]
    grouping: str
    detail: str = ""


def _positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def extract_annual_price_sales(payload: dict) -> float | None:
    metric = payload.get("metric") if isinstance(payload, dict) else None
    if not isinstance(metric, dict):
        return None
    for key in ANNUAL_PS_KEYS:
        value = _positive(metric.get(key))
        if value is not None:
            return value
    return None


def relative_score(candidate_multiple: float, peer_median: float) -> float | None:
    candidate = _positive(candidate_multiple)
    median = _positive(peer_median)
    if candidate is None or median is None:
        return None
    ratio = candidate / median
    if ratio <= 0.65:
        return 95.0
    if ratio <= 0.80:
        return 86.0
    if ratio <= 1.00:
        return 76.0
    if ratio <= 1.20:
        return 62.0
    if ratio <= 1.50:
        return 46.0
    if ratio <= 2.00:
        return 30.0
    return 15.0


def _get_json(path: str, params: dict, api_key: str, timeout: int) -> object:
    response = requests.get(
        f"{FINNHUB_BASE}{path}",
        params={**params, "token": api_key},
        headers={"User-Agent": "PEA-Analyzer-IPO-Radar/1.3"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def build_peer_benchmark(
    symbol: object,
    candidate_ps_annual: object,
    api_key: str | None,
    *,
    grouping: str = "industry",
    min_peers: int = 3,
    max_peers: int = 10,
    timeout: int = 12,
) -> PeerBenchmark:
    """Build a same-basis annual P/S benchmark from observed Finnhub peers only."""
    candidate_ps = _positive(candidate_ps_annual)
    ticker = str(symbol or "").strip().upper()
    if candidate_ps is None:
        return PeerBenchmark("SKIPPED_NO_CANDIDATE_ANNUAL_PS", None, None, None, None, 0, (), grouping)
    if not ticker:
        return PeerBenchmark("SKIPPED_NO_SYMBOL", None, candidate_ps, None, None, 0, (), grouping)
    if not api_key:
        return PeerBenchmark("SKIPPED_MISSING_KEY", None, candidate_ps, None, None, 0, (), grouping)
    try:
        raw_peers = _get_json("/stock/peers", {"symbol": ticker, "grouping": grouping}, api_key, timeout)
        if not isinstance(raw_peers, list):
            return PeerBenchmark("FAILED_PEER_SCHEMA", None, candidate_ps, None, None, 0, (), grouping)
        peers: list[str] = []
        for value in raw_peers:
            peer = str(value or "").strip().upper()
            if not peer or peer == ticker or peer in peers:
                continue
            peers.append(peer)
            if len(peers) >= max_peers:
                break
        multiples: list[tuple[str, float]] = []
        errors: list[str] = []
        for peer in peers:
            try:
                payload = _get_json("/stock/metric", {"symbol": peer, "metric": "all"}, api_key, timeout)
                multiple = extract_annual_price_sales(payload if isinstance(payload, dict) else {})
                if multiple is not None:
                    multiples.append((peer, multiple))
            except Exception as exc:
                errors.append(f"{peer}:{type(exc).__name__}")
            time.sleep(0.04)
        if len(multiples) < min_peers:
            detail = f"valid_annual_ps={len(multiples)}; peers_returned={len(peers)}"
            if errors:
                detail += f"; errors={'|'.join(errors[:4])}"
            return PeerBenchmark(
                "INSUFFICIENT_VALID_PEERS", None, candidate_ps, None, None,
                len(multiples), tuple(peer for peer, _ in multiples), grouping, detail,
            )
        peer_values = [value for _, value in multiples]
        median = float(statistics.median(peer_values))
        ratio = candidate_ps / median
        score = relative_score(candidate_ps, median)
        return PeerBenchmark(
            "SUCCESS", score, candidate_ps, round(median, 4), round(ratio, 4),
            len(multiples), tuple(peer for peer, _ in multiples), grouping,
        )
    except Exception as exc:
        return PeerBenchmark(
            "FAILED", None, candidate_ps, None, None, 0, (), grouping,
            f"{type(exc).__name__}: {str(exc)[:180]}",
        )


def add_peer_evidence(candidate: dict, api_key: str | None) -> dict:
    result = build_peer_benchmark(candidate.get("symbol"), candidate.get("sec_ipo_price_to_sales"), api_key)
    candidate["peer_valuation_status"] = result.status
    candidate["peer_valuation_source"] = "FINNHUB_REAL_PEERS_ANNUAL_PS" if result.status == "SUCCESS" else ""
    candidate["peer_grouping"] = result.grouping
    candidate["peer_count"] = result.peer_count
    candidate["peer_symbols"] = "|".join(result.peer_symbols)
    candidate["candidate_ps_annual"] = result.candidate_ps
    candidate["peer_ps_annual_median"] = result.peer_ps_median
    candidate["candidate_to_peer_ps_ratio"] = result.candidate_to_peer_ratio
    candidate["peer_valuation_detail"] = result.detail
    if result.score is not None:
        candidate["opportunity_valuation_vs_peers"] = result.score
        candidate["risk_valuation"] = round(100.0 - result.score, 2)
    return candidate
