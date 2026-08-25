from __future__ import annotations

import math
from typing import Any

from .utils import (
    annualized_volatility,
    annualized_downside_volatility,
    change_between_windows,
    clamp,
    finite,
    max_drawdown,
    mean_if,
    pct_change,
    rsi,
    safe_mean,
    safe_median,
    scale,
    sma,
    zscore_last,
    weighted_mean,
)


BLOCKS = (
    "market_regime",
    "trend_momentum",
    "liquidity_market_quality",
    "derivatives_positioning",
    "onchain_network",
    "fundamental_tokenomics",
    "catalyst_sentiment",
    "risk_quality",
)


def _log_scale(value: float | None, bad: float, good: float) -> float | None:
    if value is None or value <= 0:
        return None
    return scale(math.log10(value), math.log10(bad), math.log10(good))


def _rsi_score(value: float | None) -> float | None:
    if value is None:
        return None
    if value < 30:
        return scale(value, 15, 30)
    if value <= 50:
        scaled = scale(value, 30, 50)
        return None if scaled is None else scaled * 0.6
    if value <= 66:
        return 60.0 + (value - 50.0) * 2.5
    if value <= 78:
        return 100.0 - (value - 66.0) * 4.0
    return clamp(52.0 - (value - 78.0) * 5.0)


def _return_score(value: float | None, *, bad: float, good: float, overextended: float) -> float | None:
    if value is None:
        return None
    base = scale(value, bad, good)
    if base is None:
        return None
    if value > overextended:
        base -= min(50.0, (value - overextended) * 1.5)
    return clamp(base)


def _history(asset: dict[str, Any], as_of_ms: int) -> list[dict[str, Any]]:
    candidates = []
    previous_ts = -1
    ordered = True
    for row in asset.get("history", []):
        ts = int(row.get("ts", 0))
        if ts < previous_ts:
            ordered = False
        previous_ts = ts
        candidates.append(row)
    if not ordered:
        candidates.sort(key=lambda item: int(item.get("ts", 0)))

    valid = []
    seen: set[int] = set()
    for row in candidates:
        ts = int(row.get("ts", 0))
        price = finite(row.get("price"))
        if ts <= 0 or ts > as_of_ms or ts in seen or price is None or price <= 0:
            continue
        seen.add(ts)
        valid.append({"ts": ts, "price": price, "market_cap": finite(row.get("market_cap")), "volume": finite(row.get("volume"))})
    return valid


def _price_volume_confirmation(history: list[dict[str, Any]], window: int) -> float | None:
    pairs: list[tuple[float, float]] = []
    sample = history[-window - 1 :]
    for previous, current in zip(sample[:-1], sample[1:]):
        previous_price, current_price = previous["price"], current["price"]
        volume = current.get("volume")
        if previous_price > 0 and volume is not None and volume > 0:
            pairs.append((current_price / previous_price - 1.0, volume))
    up = [volume for change, volume in pairs if change > 0]
    down = [volume for change, volume in pairs if change < 0]
    if len(up) < 3 or len(down) < 3:
        return None
    down_mean = safe_mean(down)
    return (safe_mean(up) or 0.0) / down_mean if down_mean else None


def _amihud_proxy(history: list[dict[str, Any]], window: int = 30) -> float | None:
    observations: list[float] = []
    sample = history[-window - 1 :]
    for previous, current in zip(sample[:-1], sample[1:]):
        volume = current.get("volume")
        if previous["price"] > 0 and volume is not None and volume > 0:
            observations.append(abs(current["price"] / previous["price"] - 1.0) / volume)
    return safe_mean(observations) if len(observations) >= 10 else None


def _market_regime(price_series: dict[str, list[float]]) -> tuple[float | None, dict[str, Any]]:
    btc_prices = price_series.get("bitcoin", [])
    current, avg50, avg200 = (btc_prices[-1] if btc_prices else None), sma(btc_prices, 50), sma(btc_prices, 200)
    breadth_flags = []
    for prices in price_series.values():
        avg = sma(prices, 50)
        if prices and avg is not None:
            breadth_flags.append(100.0 if prices[-1] > avg else 0.0)
    components = [
        100.0 if current is not None and avg200 is not None and current > avg200 else (0.0 if avg200 is not None else None),
        100.0 if avg50 is not None and avg200 is not None and avg50 > avg200 else (0.0 if avg200 is not None else None),
        safe_mean(breadth_flags),
    ]
    score = mean_if(components, 2)
    return score, {"btc_price": current, "btc_sma50": avg50, "btc_sma200": avg200, "breadth_sma50_pct": safe_mean(breadth_flags)}


def build_features(snapshot: dict[str, Any], governance: dict[str, Any]) -> dict[str, Any]:
    from .utils import parse_utc

    as_of = parse_utc(snapshot["as_of"])
    as_of_ms = int(as_of.timestamp() * 1000)
    histories = {asset_id: _history(asset, as_of_ms) for asset_id, asset in snapshot["assets"].items()}
    price_series = {asset_id: [row["price"] for row in history] for asset_id, history in histories.items()}
    btc_prices = price_series.get("bitcoin", [])
    btc_ret30, btc_ret90 = pct_change(btc_prices, 30), pct_change(btc_prices, 90)
    regime_score, regime_metrics = _market_regime(price_series)
    result: dict[str, Any] = {}
    for asset_id, asset in snapshot["assets"].items():
        history = histories[asset_id]
        prices = price_series[asset_id]
        volumes = [row["volume"] for row in history if row["volume"] is not None]
        market = asset.get("market", {})
        current = prices[-1] if prices else finite(market.get("price"))
        avg20, avg50, avg200 = sma(prices, 20), sma(prices, 50), sma(prices, 200)
        ret7, ret30, ret90 = pct_change(prices, 7), pct_change(prices, 30), pct_change(prices, 90)
        rsi14 = rsi(prices, 14)
        high55 = max(prices[-56:-1]) if len(prices) >= 56 else None
        breakout55 = ((current / high55 - 1.0) * 100.0) if current and high55 else None
        volatility = annualized_volatility(prices, 30)
        downside_volatility = annualized_downside_volatility(prices, 30)
        drawdown = max_drawdown(prices, 90)
        momentum_acceleration_tct = ret7 - ret30 * 7.0 / 30.0 if ret7 is not None and ret30 is not None else None
        momentum_acceleration_ct = ret30 - ret90 / 3.0 if ret30 is not None and ret90 is not None else None
        price_volume_tct = _price_volume_confirmation(history, 30)
        price_volume_ct = _price_volume_confirmation(history, 90)
        btc_relative_tct = ret30 - btc_ret30 if ret30 is not None and btc_ret30 is not None else None
        btc_relative_ct = ret90 - btc_ret90 if ret90 is not None and btc_ret90 is not None else None

        trend_tct_base = mean_if([
            100.0 if current and avg20 and current > avg20 else (0.0 if avg20 else None),
            100.0 if current and avg50 and current > avg50 else (0.0 if avg50 else None),
            _return_score(ret7, bad=-12.0, good=12.0, overextended=22.0),
            _return_score(ret30, bad=-25.0, good=28.0, overextended=45.0),
            _rsi_score(rsi14),
            scale(breakout55, -15.0, 2.0),
        ], 4)
        trend_ct_base = mean_if([
            100.0 if current and avg50 and current > avg50 else (0.0 if avg50 else None),
            100.0 if current and avg200 and current > avg200 else (0.0 if avg200 else None),
            100.0 if avg50 and avg200 and avg50 > avg200 else (0.0 if avg200 else None),
            _return_score(ret30, bad=-30.0, good=35.0, overextended=55.0),
            _return_score(ret90, bad=-45.0, good=80.0, overextended=130.0),
            _rsi_score(rsi14),
        ], 4)
        incremental = governance["incremental_criteria_weights"]
        trend_tct = weighted_mean([
            (trend_tct_base, incremental["TCT"]["trend_momentum"]["existing_basket"]),
            (scale(momentum_acceleration_tct, -5.0, 8.0), incremental["TCT"]["trend_momentum"]["momentum_acceleration"]),
            (scale(price_volume_tct, 0.7, 1.5), incremental["TCT"]["trend_momentum"]["price_volume_confirmation"]),
            (scale(btc_relative_tct, -20.0, 20.0), incremental["TCT"]["trend_momentum"]["btc_relative_strength"]),
        ])
        trend_ct = weighted_mean([
            (trend_ct_base, incremental["CT"]["trend_momentum"]["existing_basket"]),
            (scale(momentum_acceleration_ct, -12.0, 20.0), incremental["CT"]["trend_momentum"]["momentum_acceleration"]),
            (scale(price_volume_ct, 0.7, 1.5), incremental["CT"]["trend_momentum"]["price_volume_confirmation"]),
            (scale(btc_relative_ct, -40.0, 40.0), incremental["CT"]["trend_momentum"]["btc_relative_strength"]),
        ])

        market_cap = finite(market.get("market_cap"))
        volume24 = finite(market.get("volume_24h"))
        median_volume = safe_median(volumes[-30:])
        relative_volume = (volumes[-1] / median_volume) if volumes and median_volume else None
        amihud = _amihud_proxy(history)
        amihud_score = _log_scale(amihud, 1e-8, 1e-12)
        turnover = (volume24 / market_cap) if volume24 is not None and market_cap else None
        venue_prices: list[float] = []
        for venue in asset.get("venues", {}).values():
            venue_price = finite(venue.get("price"))
            if venue_price is not None and venue_price > 0:
                venue_prices.append(venue_price)
        if current:
            venue_prices.append(current)
        divergence = ((max(venue_prices) / min(venue_prices) - 1.0) * 100.0) if len(venue_prices) >= 2 else None
        spreads = [finite(row.get("spread_bps")) for row in asset.get("venues", {}).values()]
        spread_bps = safe_mean(spreads)
        liquidity_tct_base = mean_if([
            _log_scale(market_cap, 250_000_000, 30_000_000_000),
            _log_scale(median_volume, 10_000_000, 1_000_000_000),
            scale(turnover, 0.005, 0.12),
            100.0 - clamp((spread_bps or 0.0) * 8.0) if spread_bps is not None else None,
            scale(relative_volume, 0.5, 2.0) if relative_volume is not None else None,
        ], 3)
        liquidity_ct_base = mean_if([
            _log_scale(market_cap, 250_000_000, 30_000_000_000),
            _log_scale(median_volume, 10_000_000, 1_000_000_000),
            scale(turnover, 0.003, 0.08),
            100.0 - clamp((spread_bps or 0.0) * 8.0) if spread_bps is not None else None,
        ], 3)
        liquidity_tct = weighted_mean([
            (liquidity_tct_base, incremental["TCT"]["liquidity_market_quality"]["existing_basket"]),
            (amihud_score, incremental["TCT"]["liquidity_market_quality"]["amihud_illiquidity_proxy"]),
        ])
        liquidity_ct = weighted_mean([
            (liquidity_ct_base, incremental["CT"]["liquidity_market_quality"]["existing_basket"]),
            (amihud_score, incremental["CT"]["liquidity_market_quality"]["amihud_illiquidity_proxy"]),
        ])

        derivatives = asset.get("derivatives", {})
        funding = finite(derivatives.get("last_funding_rate"))
        funding_series = [value for value in (finite(item) for item in derivatives.get("funding_history", [])) if value is not None]
        funding_z = zscore_last(funding_series)
        funding_score = None if funding is None else clamp(100.0 - abs(funding) / 0.0015 * 100.0)
        derivatives_tct = safe_mean([funding_score, None if funding_z is None else clamp(100.0 - abs(funding_z) * 25.0)])
        derivatives_ct = safe_mean([funding_score, None if funding_z is None else clamp(100.0 - abs(funding_z) * 18.0)])

        network = asset.get("network", {})
        cm = network.get("coinmetrics", {})
        active_change = change_between_windows(cm.get("AdrActCnt", []), 30)
        tx_change = change_between_windows(cm.get("TxCnt", []), 30)
        fees_change = change_between_windows(cm.get("FeeTotUSD", []), 30)
        tvl_change = pct_change(network.get("chain_tvl", []), 30)
        onchain_tct = mean_if([scale(active_change, -20.0, 25.0), scale(tvl_change, -25.0, 35.0)], 1)
        onchain_ct = mean_if([
            scale(active_change, -25.0, 35.0),
            scale(tx_change, -25.0, 35.0),
            scale(fees_change, -50.0, 80.0),
            scale(tvl_change, -30.0, 50.0),
        ], 2)

        fdv = finite(market.get("fdv"))
        circulating = finite(market.get("circulating_supply"))
        total_supply = finite(market.get("total_supply")) or finite(market.get("max_supply"))
        mcap_fdv = market_cap / fdv if market_cap is not None and fdv else None
        supply_ratio = circulating / total_supply if circulating is not None and total_supply else None
        fundamentals_tct = mean_if([_log_scale(market_cap, 250_000_000, 50_000_000_000), scale(len(history), 200, 365)], 2)
        fundamentals_ct = mean_if([
            _log_scale(market_cap, 250_000_000, 50_000_000_000),
            scale(mcap_fdv, 0.25, 1.0),
            scale(supply_ratio, 0.25, 1.0),
            scale(len(history), 200, 365),
        ], 3)

        evidence = asset.get("evidence", [])
        catalyst_values = [finite(row.get("score")) for row in evidence if str(row.get("type", "")).upper() != "SECURITY_INCIDENT"]
        catalyst_score = safe_mean(catalyst_values)
        volatility_scaled = scale(volatility, 35.0, 180.0) if volatility is not None else None
        risk_base = mean_if([
            None if volatility_scaled is None else 100.0 - volatility_scaled,
            None if drawdown is None else scale(drawdown, -60.0, -5.0),
        ], 2)
        downside_scaled = scale(downside_volatility, 25.0, 140.0) if downside_volatility is not None else None
        risk_tct = weighted_mean([
            (risk_base, incremental["TCT"]["risk_quality"]["existing_basket"]),
            (None if downside_scaled is None else 100.0 - downside_scaled, incremental["TCT"]["risk_quality"]["downside_volatility"]),
        ])
        risk_ct = weighted_mean([
            (risk_base, incremental["CT"]["risk_quality"]["existing_basket"]),
            (None if downside_scaled is None else 100.0 - downside_scaled, incremental["CT"]["risk_quality"]["downside_volatility"]),
        ])

        hard_incident = any(
            str(row.get("type", "")).upper() == "SECURITY_INCIDENT"
            and str(row.get("severity", "")).upper() in governance["risk_gates"]["hard_incident_severities"]
            for row in evidence
        )
        category = str(asset.get("spec", {}).get("category", "")).upper()
        universe_flags = []
        market_age_hours = None
        market_updated = market.get("last_updated")
        if market_updated:
            try:
                market_time = parse_utc(str(market_updated))
                market_age_hours = (as_of - market_time).total_seconds() / 3600.0
                if market_age_hours < -0.01:
                    universe_flags.append("FUTURE_MARKET_TIMESTAMP")
                elif market_age_hours > governance["risk_gates"]["market_data_max_age_hours"]:
                    universe_flags.append("STALE_MARKET_DATA")
            except ValueError:
                universe_flags.append("INVALID_MARKET_TIMESTAMP")
        if category in {"STABLECOIN", "WRAPPED", "LEVERAGED"}:
            universe_flags.append(f"FORBIDDEN_CATEGORY:{category}")
        if market_cap is None or market_cap < governance["universe_gates"]["minimum_market_cap_usd"]:
            universe_flags.append("MARKET_CAP_GATE")
        if median_volume is None or median_volume < governance["universe_gates"]["minimum_median_daily_volume_usd"]:
            universe_flags.append("LIQUIDITY_GATE")
        if len(history) < governance["universe_gates"]["minimum_history_days"]:
            universe_flags.append("HISTORY_GATE")
        hard_risk_flags = ["SECURITY_INCIDENT"] if hard_incident else []
        if divergence is not None and divergence > governance["risk_gates"]["source_divergence_block_pct"]:
            hard_risk_flags.append("SOURCE_DIVERGENCE")
        soft_risk_flags = []
        if volatility is not None and volatility > governance["risk_gates"]["annualized_volatility_wait_pct"]:
            soft_risk_flags.append("EXTREME_VOLATILITY")
        if drawdown is not None and drawdown < governance["risk_gates"]["drawdown_90d_wait_pct"]:
            soft_risk_flags.append("DRAWDOWN_RISK")
        if funding is not None and abs(funding) > governance["risk_gates"]["extreme_funding_abs_8h"]:
            soft_risk_flags.append("EXTREME_FUNDING")

        source_count = 1 if market else 0
        source_count += sum(bool(value) for value in asset.get("venues", {}).values())
        source_count += int(bool(cm)) + int(bool(network.get("chain_tvl"))) + int(bool(evidence))
        agreement = 60.0 if divergence is None else clamp(100.0 - divergence * 12.0)
        history_quality = clamp(len(history) / 365.0 * 100.0)
        data_quality = safe_mean([history_quality, 100.0 if len(history) == len({row["ts"] for row in history}) else 0.0, agreement]) or 0.0

        result[asset_id] = {
            "asset_id": asset_id,
            "symbol": asset["spec"]["symbol"],
            "name": asset["spec"]["name"],
            "blocks": {
                "TCT": {
                    "market_regime": regime_score,
                    "trend_momentum": trend_tct,
                    "liquidity_market_quality": liquidity_tct,
                    "derivatives_positioning": derivatives_tct,
                    "onchain_network": onchain_tct,
                    "fundamental_tokenomics": fundamentals_tct,
                    "catalyst_sentiment": catalyst_score,
                    "risk_quality": risk_tct,
                },
                "CT": {
                    "market_regime": regime_score,
                    "trend_momentum": trend_ct,
                    "liquidity_market_quality": liquidity_ct,
                    "derivatives_positioning": derivatives_ct,
                    "onchain_network": onchain_ct,
                    "fundamental_tokenomics": fundamentals_ct,
                    "catalyst_sentiment": catalyst_score,
                    "risk_quality": risk_ct,
                },
            },
            "metrics": {
                **regime_metrics,
                "price": current,
                "market_cap_usd": market_cap,
                "market_cap_rank": finite(market.get("market_cap_rank")),
                "median_daily_volume_usd_30d": median_volume,
                "return_7d_pct": ret7,
                "return_30d_pct": ret30,
                "return_90d_pct": ret90,
                "rsi14": rsi14,
                "annualized_volatility_30d_pct": volatility,
                "annualized_downside_volatility_30d_pct": downside_volatility,
                "max_drawdown_90d_pct": drawdown,
                "momentum_acceleration_tct_pct": momentum_acceleration_tct,
                "momentum_acceleration_ct_pct": momentum_acceleration_ct,
                "price_volume_confirmation_30d": price_volume_tct,
                "price_volume_confirmation_90d": price_volume_ct,
                "btc_relative_strength_30d_pct": btc_relative_tct,
                "btc_relative_strength_90d_pct": btc_relative_ct,
                "amihud_illiquidity_proxy_30d": amihud,
                "spread_bps": spread_bps,
                "source_divergence_pct": divergence,
                "funding_rate_8h": funding,
                "funding_zscore_30": funding_z,
                "active_addresses_change_30d_pct": active_change,
                "transactions_change_30d_pct": tx_change,
                "fees_change_30d_pct": fees_change,
                "chain_tvl_change_30d_pct": tvl_change,
                "market_cap_to_fdv": mcap_fdv,
                "circulating_supply_ratio": supply_ratio,
                "history_days": len(history),
                "market_age_hours": market_age_hours,
            },
            "universe_flags": universe_flags,
            "hard_risk_flags": hard_risk_flags,
            "soft_risk_flags": soft_risk_flags,
            "source_count": source_count,
            "source_agreement": agreement,
            "data_quality": data_quality,
        }
    return result
