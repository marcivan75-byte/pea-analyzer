from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import time
from typing import Any, Callable

from .http import JsonHttpClient, SourceError
from .utils import finite, iso_z, parse_utc, utc_now


def _parallel(items: list[Any], worker: Callable[[Any], tuple[str, Any]], max_workers: int) -> tuple[dict[str, Any], list[str]]:
    results: dict[str, Any] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                key, value = future.result()
                results[key] = value
            except Exception as exc:  # failures are explicit per source/asset and never bearish
                item_id = item.get("id", str(item)) if isinstance(item, dict) else str(item)
                errors.append(f"{item_id}:{type(exc).__name__}")
    return results, sorted(errors)


class CryptoCollector:
    def __init__(self, client: JsonHttpClient, root: Path, config: dict[str, Any]):
        self.client = client
        self.root = root
        self.config = config
        self.workers = int(config["runtime"]["max_workers"])

    @staticmethod
    def _coingecko_access() -> tuple[str, dict[str, str], float]:
        pro_key = os.environ.get("COINGECKO_PRO_API_KEY", "").strip()
        if pro_key:
            return "https://pro-api.coingecko.com/api/v3", {"x-cg-pro-api-key": pro_key}, 8.0
        demo_key = os.environ.get("COINGECKO_API_KEY", "").strip()
        headers = {"x-cg-demo-api-key": demo_key} if demo_key else {}
        return "https://api.coingecko.com/api/v3", headers, 0.4

    def discover_top_market_cap(
        self, universe_config: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Resolve the point-in-time Top N by CoinGecko market-cap rank.

        Static entries are identity/source overrides only. They never replace or pad
        the observed ranking, so a partial discovery fails closed.
        """
        target = int(universe_config.get("target_count", 100))
        base, headers, rate = self._coingecko_access()
        rows = self.client.get_json(
            f"{base}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": target,
                "page": 1,
                "sparkline": "false",
            },
            headers=headers,
            namespace="coingecko_top_market_cap",
            ttl_seconds=1800,
            requests_per_second=rate,
        )
        if not isinstance(rows, list):
            raise SourceError("TOP100_DISCOVERY_INVALID_PAYLOAD")

        overrides = {str(row["id"]): row for row in universe_config.get("assets", []) if row.get("id")}
        exclusions = universe_config.get("classification_overrides", {})
        specs: list[dict[str, Any]] = []
        usable_rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for position, market_row in enumerate(rows, start=1):
            if not isinstance(market_row, dict) or not market_row.get("id"):
                continue
            asset_id = str(market_row["id"])
            if asset_id in seen:
                continue
            seen.add(asset_id)
            rank_value = finite(market_row.get("market_cap_rank"))
            rank = int(rank_value) if rank_value is not None else position
            seed = dict(overrides.get(asset_id, {}))
            spec: dict[str, Any] = {
                "id": asset_id,
                "symbol": str(market_row.get("symbol") or seed.get("symbol") or "").upper(),
                "name": str(market_row.get("name") or seed.get("name") or asset_id),
                "market_cap_rank": rank,
                "category": str(exclusions.get(asset_id) or seed.get("category") or "DYNAMIC_TOP100"),
                "contract": seed.get("contract"),
                "identity_source": "COINGECKO_TOP_MARKET_CAP",
            }
            for key in ("binance", "kraken", "coinmetrics", "glassnode", "chain"):
                if seed.get(key):
                    spec[key] = seed[key]
            specs.append(spec)
            usable_rows.append(market_row)
            if len(specs) == target:
                break
        if len(specs) != target or len({row["id"] for row in specs}) != target:
            raise SourceError(f"TOP100_DISCOVERY_INCOMPLETE:{len(specs)}/{target}")
        return specs, usable_rows

    def collect(
        self,
        universe: list[dict[str, Any]],
        as_of: str | None = None,
        *,
        preloaded_market_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        observed_at = parse_utc(as_of) if as_of else utc_now()
        assets = {spec["id"]: {"spec": spec, "history": [], "market": {}, "venues": {}, "derivatives": {}, "network": {}, "evidence": []} for spec in universe}
        status: dict[str, Any] = {}
        provider_seconds: dict[str, float] = {}

        jobs: dict[str, Callable[[], None]] = {
            "COINGECKO": lambda: self._collect_coingecko(universe, assets, status, preloaded_market_rows),
            "BINANCE_PUBLIC": lambda: self._collect_binance(universe, assets, status),
            "KRAKEN_PUBLIC": lambda: self._collect_kraken(universe, assets, status),
            "COIN_METRICS_COMMUNITY": lambda: self._collect_coinmetrics(universe, assets, status, observed_at),
            "DEFILLAMA": lambda: self._collect_defillama(universe, assets, status),
        }

        def timed_provider(item: tuple[str, Callable[[], None]]) -> tuple[str, float, str | None]:
            provider, operation = item
            started = time.perf_counter()
            try:
                operation()
                return provider, time.perf_counter() - started, None
            except Exception as exc:  # unexpected provider failures remain isolated and explicit
                return provider, time.perf_counter() - started, type(exc).__name__

        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            futures = [executor.submit(timed_provider, item) for item in jobs.items()]
            for future in as_completed(futures):
                provider, elapsed, error = future.result()
                provider_seconds[provider] = round(elapsed, 6)
                if error:
                    status[provider] = {"state": "FAILED", "errors": [f"UNEXPECTED:{error}"]}
        self._load_evidence(assets, status, observed_at)

        return {
            "schema_version": "CRYPTO_SNAPSHOT_V1",
            "as_of": iso_z(observed_at),
            "assets": assets,
            "source_status": status,
            "collection_runtime": {"provider_seconds": dict(sorted(provider_seconds.items()))},
        }

    def _collect_coingecko(
        self,
        universe: list[dict[str, Any]],
        assets: dict[str, Any],
        status: dict[str, Any],
        preloaded_market_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        base, headers, rate = self._coingecko_access()
        ids = ",".join(spec["id"] for spec in universe)
        try:
            rows = preloaded_market_rows
            if rows is None:
                rows = self.client.get_json(
                    f"{base}/coins/markets",
                    params={"vs_currency": "usd", "ids": ids, "order": "market_cap_desc", "sparkline": "false"},
                    headers=headers,
                    namespace="coingecko",
                    ttl_seconds=1800,
                    requests_per_second=rate,
                )
            for row in rows:
                asset_id = row.get("id")
                if asset_id in assets:
                    assets[asset_id]["market"] = {
                        "price": finite(row.get("current_price")),
                        "market_cap": finite(row.get("market_cap")),
                        "market_cap_rank": finite(row.get("market_cap_rank")),
                        "fdv": finite(row.get("fully_diluted_valuation")),
                        "volume_24h": finite(row.get("total_volume")),
                        "circulating_supply": finite(row.get("circulating_supply")),
                        "total_supply": finite(row.get("total_supply")),
                        "max_supply": finite(row.get("max_supply")),
                        "last_updated": row.get("last_updated"),
                        "source": "COINGECKO",
                    }
        except SourceError as exc:
            status["COINGECKO"] = {"state": "FAILED", "errors": [str(exc)]}
            return

        def history_worker(spec: dict[str, Any]) -> tuple[str, Any]:
            payload = self.client.get_json(
                f"{base}/coins/{spec['id']}/market_chart",
                params={"vs_currency": "usd", "days": 365, "interval": "daily"},
                headers=headers,
                namespace="coingecko",
                ttl_seconds=21600,
                requests_per_second=rate,
            )
            caps = {int(item[0]): finite(item[1]) for item in payload.get("market_caps", [])}
            volumes = {int(item[0]): finite(item[1]) for item in payload.get("total_volumes", [])}
            history = []
            for timestamp, price in payload.get("prices", []):
                ts = int(timestamp)
                clean_price = finite(price)
                if clean_price is not None and clean_price > 0:
                    history.append({
                        "ts": ts,
                        "price": clean_price,
                        "market_cap": caps.get(ts),
                        "volume": volumes.get(ts),
                    })
            return spec["id"], history

        histories, errors = _parallel(universe, history_worker, self.workers)
        for asset_id, history in histories.items():
            assets[asset_id]["history"] = history
        status["COINGECKO"] = {
            "state": "OK" if not errors else "PARTIAL",
            "market_rows": sum(bool(asset["market"]) for asset in assets.values()),
            "history_rows": sum(bool(asset["history"]) for asset in assets.values()),
            "errors": errors,
        }

    def _collect_binance(self, universe: list[dict[str, Any]], assets: dict[str, Any], status: dict[str, Any]) -> None:
        spot = "https://data-api.binance.vision/api/v3"
        futures = "https://fapi.binance.com"
        ticker_by_symbol: dict[str, Any] = {}
        book_by_symbol: dict[str, Any] = {}
        try:
            exchange_info = self.client.get_json(
                f"{spot}/exchangeInfo", namespace="binance", ttl_seconds=21600, requests_per_second=2.0
            )
            tickers = self.client.get_json(
                f"{spot}/ticker/24hr", namespace="binance", ttl_seconds=900, requests_per_second=2.0
            )
            books = self.client.get_json(
                f"{spot}/ticker/bookTicker", namespace="binance", ttl_seconds=300, requests_per_second=2.0
            )
            ticker_by_symbol = {
                str(row["symbol"]): row for row in tickers if isinstance(row, dict) and isinstance(row.get("symbol"), str)
            }
            book_by_symbol = {
                str(row["symbol"]): row for row in books if isinstance(row, dict) and isinstance(row.get("symbol"), str)
            }
            symbol_counts: dict[str, int] = {}
            for spec in universe:
                candidate = str(spec.get("symbol") or "").upper()
                symbol_counts[candidate] = symbol_counts.get(candidate, 0) + 1
            active_usdt = {
                str(row.get("baseAsset")): str(row.get("symbol"))
                for row in exchange_info.get("symbols", [])
                if isinstance(row, dict)
                and row.get("status") == "TRADING"
                and row.get("quoteAsset") == "USDT"
                and isinstance(row.get("baseAsset"), str)
                and isinstance(row.get("symbol"), str)
            }
            dynamic_mappings = 0
            for spec in universe:
                base_symbol = str(spec.get("symbol") or "").upper()
                if not spec.get("binance") and symbol_counts.get(base_symbol) == 1 and base_symbol in active_usdt:
                    spec["binance"] = active_usdt[base_symbol]
                    spec["binance_mapping_method"] = "UNIQUE_TOP100_SYMBOL_BINANCE_SPOT"
                    dynamic_mappings += 1
                symbol = str(spec.get("binance") or "")
                ticker = ticker_by_symbol.get(symbol, {})
                book = book_by_symbol.get(symbol, {})
                bid, ask = finite(book.get("bidPrice")), finite(book.get("askPrice"))
                midpoint = (bid + ask) / 2 if bid is not None and ask is not None else None
                assets[spec["id"]]["venues"]["binance"] = {
                    "price": finite(ticker.get("lastPrice")),
                    "quote_volume_24h": finite(ticker.get("quoteVolume")),
                    "bid": bid,
                    "ask": ask,
                    "spread_bps": ((ask - bid) / midpoint * 10000.0) if bid is not None and ask is not None and midpoint else None,
                }
        except SourceError as exc:
            status["BINANCE_PUBLIC"] = {"state": "FAILED", "errors": [str(exc)]}
            return

        try:
            premium = self.client.get_json(
                f"{futures}/fapi/v1/premiumIndex", namespace="binance_futures", ttl_seconds=300, requests_per_second=2.0
            )
            premium_by_symbol = {row.get("symbol"): row for row in premium if isinstance(row, dict)}
        except SourceError:
            premium_by_symbol = {}

        futures_symbols = {str(symbol) for symbol in premium_by_symbol if symbol}

        def funding_worker(spec: dict[str, Any]) -> tuple[str, Any]:
            symbol = spec.get("binance")
            if not symbol:
                return spec["id"], []
            rows = self.client.get_json(
                f"{futures}/fapi/v1/fundingRate",
                params={"symbol": symbol, "limit": 90},
                namespace="binance_futures",
                ttl_seconds=3600,
                requests_per_second=2.0,
            )
            return spec["id"], [finite(row.get("fundingRate")) for row in rows if finite(row.get("fundingRate")) is not None]

        funding_specs = [spec for spec in universe if str(spec.get("binance") or "") in futures_symbols]
        funding, errors = _parallel(funding_specs, funding_worker, self.workers)
        for spec in universe:
            symbol = str(spec.get("binance") or "")
            premium_row = premium_by_symbol.get(symbol, {})
            assets[spec["id"]]["derivatives"] = {
                "last_funding_rate": finite(premium_row.get("lastFundingRate")),
                "mark_price": finite(premium_row.get("markPrice")),
                "funding_history": funding.get(spec["id"], []),
            }
        status["BINANCE_PUBLIC"] = {
            "state": "OK" if not errors else "PARTIAL",
            "spot_assets": sum(bool(asset["venues"].get("binance")) for asset in assets.values()),
            "funding_assets": sum(bool(asset["derivatives"].get("funding_history")) for asset in assets.values()),
            "dynamic_symbol_mappings": dynamic_mappings,
            "mapped_assets": sum(bool(spec.get("binance")) for spec in universe),
            "errors": errors,
        }

    def _collect_kraken(self, universe: list[dict[str, Any]], assets: dict[str, Any], status: dict[str, Any]) -> None:
        pairs = [spec["kraken"] for spec in universe if spec.get("kraken")]
        if not pairs:
            status["KRAKEN_PUBLIC"] = {"state": "SKIPPED", "errors": []}
            return
        try:
            mappings = self.client.get_json(
                "https://api.kraken.com/0/public/AssetPairs",
                namespace="kraken",
                ttl_seconds=86400,
                requests_per_second=1.0,
            )
            alt_to_key = {value.get("altname"): key for key, value in mappings.get("result", {}).items()}
            payload = self.client.get_json(
                "https://api.kraken.com/0/public/Ticker",
                params={"pair": ",".join(pairs)},
                namespace="kraken",
                ttl_seconds=900,
                requests_per_second=1.0,
            )
            result = payload.get("result", {})
            for spec in universe:
                pair = spec.get("kraken")
                row = result.get(alt_to_key.get(pair, ""), {})
                if not row:
                    continue
                ask = finite((row.get("a") or [None])[0])
                bid = finite((row.get("b") or [None])[0])
                last = finite((row.get("c") or [None])[0])
                midpoint = (bid + ask) / 2 if bid is not None and ask is not None else None
                assets[spec["id"]]["venues"]["kraken"] = {
                    "price": last,
                    "bid": bid,
                    "ask": ask,
                    "spread_bps": ((ask - bid) / midpoint * 10000.0) if bid is not None and ask is not None and midpoint else None,
                }
            status["KRAKEN_PUBLIC"] = {"state": "OK", "assets": sum(bool(a["venues"].get("kraken")) for a in assets.values()), "errors": []}
        except SourceError as exc:
            status["KRAKEN_PUBLIC"] = {"state": "FAILED", "errors": [str(exc)]}

    def _collect_coinmetrics(
        self, universe: list[dict[str, Any]], assets: dict[str, Any], status: dict[str, Any], observed_at: datetime
    ) -> None:
        mapped: dict[str, str] = {}
        for spec in universe:
            coinmetrics_id = spec.get("coinmetrics")
            if isinstance(coinmetrics_id, str) and coinmetrics_id:
                mapped[coinmetrics_id] = str(spec["id"])
        if not mapped:
            return
        try:
            payload = self.client.get_json(
                "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
                params={
                    "assets": ",".join(mapped),
                    "metrics": "AdrActCnt,TxCnt,FeeTotUSD",
                    "frequency": "1d",
                    "start_time": iso_z(observed_at - timedelta(days=90))[:10],
                    "end_time": iso_z(observed_at)[:10],
                    "page_size": 10000,
                },
                namespace="coinmetrics",
                ttl_seconds=21600,
                requests_per_second=0.5,
            )
            for row in payload.get("data", []):
                asset_id = mapped.get(row.get("asset"))
                if not asset_id:
                    continue
                series = assets[asset_id]["network"].setdefault("coinmetrics", {"AdrActCnt": [], "TxCnt": [], "FeeTotUSD": []})
                for metric in series:
                    value = finite(row.get(metric))
                    if value is not None:
                        series[metric].append(value)
            status["COIN_METRICS_COMMUNITY"] = {
                "state": "OK",
                "assets": sum(bool(a["network"].get("coinmetrics")) for a in assets.values()),
                "errors": [],
            }
        except SourceError as exc:
            status["COIN_METRICS_COMMUNITY"] = {"state": "FAILED", "errors": [str(exc)]}

    def _collect_defillama(self, universe: list[dict[str, Any]], assets: dict[str, Any], status: dict[str, Any]) -> None:
        mapped = [spec for spec in universe if spec.get("chain")]

        def worker(spec: dict[str, Any]) -> tuple[str, Any]:
            rows = self.client.get_json(
                f"https://api.llama.fi/v2/historicalChainTvl/{spec['chain']}",
                namespace="defillama",
                ttl_seconds=21600,
                requests_per_second=1.0,
            )
            return spec["id"], [finite(row.get("tvl")) for row in rows[-90:] if finite(row.get("tvl")) is not None]

        results, errors = _parallel(mapped, worker, self.workers)
        for asset_id, series in results.items():
            assets[asset_id]["network"]["chain_tvl"] = series
        status["DEFILLAMA"] = {"state": "OK" if not errors else "PARTIAL", "assets": len(results), "errors": errors}

    def _load_evidence(self, assets: dict[str, Any], status: dict[str, Any], observed_at: Any) -> None:
        path = self.root / "data" / "manual" / "PIT_EVIDENCE.json"
        if not path.exists():
            status["PIT_MANUAL_EVIDENCE"] = {"state": "ABSENT", "rows": 0, "errors": []}
            return
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            accepted = 0
            for row in rows:
                required = {"asset_id", "observed_at", "effective_at", "expires_at", "source_url", "severity"}
                if not required.issubset(row) or row["asset_id"] not in assets:
                    continue
                seen = parse_utc(row["observed_at"])
                effective = parse_utc(row["effective_at"])
                expires = parse_utc(row["expires_at"])
                if seen > observed_at or expires < observed_at or expires < effective:
                    continue
                if not str(row["source_url"]).startswith("https://"):
                    continue
                assets[row["asset_id"]]["evidence"].append(row)
                accepted += 1
            status["PIT_MANUAL_EVIDENCE"] = {"state": "OK", "rows": accepted, "errors": []}
        except (OSError, ValueError, TypeError) as exc:
            status["PIT_MANUAL_EVIDENCE"] = {"state": "FAILED", "rows": 0, "errors": [type(exc).__name__]}
