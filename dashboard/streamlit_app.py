from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import pandas as pd
import streamlit as st

DEFAULT_API_URL = os.getenv("STORE_INTEL_API_URL", "http://localhost:8000")
DEFAULT_STORE_ID = os.getenv("STORE_INTEL_DASHBOARD_STORE_ID", "STORE_BLR_002")


@dataclass(frozen=True)
class ApiResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None


def fetch_json(api_url: str, path: str) -> ApiResult:
    url = f"{api_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return ApiResult(ok=False, error="API returned a non-object JSON payload.")
            return ApiResult(ok=True, data=payload)
    except httpx.HTTPStatusError as exc:
        return ApiResult(
            ok=False,
            error=f"{exc.response.status_code} from {url}: {exc.response.text[:240]}",
        )
    except httpx.RequestError as exc:
        return ApiResult(ok=False, error=f"Could not reach {url}: {exc}")
    except ValueError as exc:
        return ApiResult(ok=False, error=f"Invalid JSON from {url}: {exc}")


def render_metric_cards(metrics: dict[str, Any]) -> None:
    queue = metrics.get("queue_depth") or {}
    cols = st.columns(5)
    cols[0].metric("Visitors", metrics.get("unique_visitors", 0))
    cols[1].metric("Conversion", f"{float(metrics.get('conversion_rate', 0.0)) * 100:.1f}%")
    cols[2].metric("Queue Depth", queue.get("current_depth", queue.get("current", 0)))
    cols[3].metric("Avg Wait", f"{float(queue.get('avg_wait_time', 0.0)) / 1000:.0f}s")
    cols[4].metric("Abandonment", f"{float(metrics.get('abandonment_rate', 0.0)) * 100:.1f}%")


def render_visitor_trend(summary: dict[str, Any]) -> None:
    st.subheader("Visitor Trend")
    points = summary.get("visitor_trend") or []
    if not points:
        st.info("No visitor trend yet. Ingest ENTRY or REENTRY events to populate this chart.")
        return
    frame = pd.DataFrame(points)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    chart = frame.set_index("timestamp")[["visitors"]]
    st.line_chart(chart, height=260)


def render_conversion_chart(summary: dict[str, Any]) -> None:
    st.subheader("Conversion Trend")
    points = summary.get("conversion_trend") or []
    if not points:
        st.info("No conversion trend yet. Purchase attribution events will populate this chart.")
        return
    frame = pd.DataFrame(points)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["conversion_pct"] = frame["conversion_rate"].astype(float) * 100
    st.line_chart(frame.set_index("timestamp")[["conversion_pct"]], height=260)


def render_zone_dwell_chart(metrics: dict[str, Any]) -> None:
    st.subheader("Zone Dwell")
    zones = metrics.get("avg_dwell_per_zone") or []
    if not zones:
        st.info("No zone dwell events available yet.")
        return
    frame = pd.DataFrame(zones)
    frame["avg_dwell_sec"] = frame["avg_dwell_ms"].astype(float) / 1000
    chart = frame.sort_values("avg_dwell_sec", ascending=False).set_index("zone_id")[
        ["avg_dwell_sec"]
    ]
    st.bar_chart(chart, height=280)


def render_funnel(funnel: dict[str, Any]) -> None:
    st.subheader("Conversion Funnel")
    stages = funnel.get("stages") or []
    if not stages:
        st.info("No funnel events available yet.")
        return
    frame = pd.DataFrame(stages)
    st.bar_chart(frame.set_index("stage")[["count"]], height=280)
    st.dataframe(
        frame[["stage", "count", "dropoff_pct", "conversion_pct"]].rename(
            columns={
                "stage": "Stage",
                "count": "Count",
                "dropoff_pct": "Drop-off %",
                "conversion_pct": "Conversion %",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )


def render_queue_chart(metrics: dict[str, Any]) -> None:
    st.subheader("Queue Analytics")
    queue = metrics.get("queue_depth") or {}
    values = {
        "Current depth": int(queue.get("current_depth", 0) or 0),
        "Max depth": int(queue.get("max_depth", 0) or 0),
        "Avg wait sec": round(float(queue.get("avg_wait_time", 0.0) or 0.0) / 1000, 1),
        "Peak wait sec": round(float(queue.get("peak_wait_time", 0) or 0) / 1000, 1),
    }
    if not any(values.values()):
        st.info("No queue activity detected for CASH_COUNTER yet.")
        return
    frame = pd.DataFrame({"Metric": values.keys(), "Value": values.values()}).set_index("Metric")
    st.bar_chart(frame, height=260)


def render_top_zones(metrics: dict[str, Any], heatmap: dict[str, Any]) -> None:
    st.subheader("Top Zones")
    dwell_zones = {item.get("zone_id"): item for item in metrics.get("avg_dwell_per_zone") or []}
    heatmap_zones = heatmap.get("zones") or []
    if not dwell_zones and not heatmap_zones:
        st.info("No zone activity available yet.")
        return
    rows: list[dict[str, Any]] = []
    for zone in heatmap_zones:
        zone_id = zone.get("zone_id")
        dwell = dwell_zones.get(zone_id, {})
        rows.append(
            {
                "Zone": zone_id,
                "Visits": zone.get("visit_count", 0),
                "Visitors": dwell.get("visitor_count", 0),
                "Avg dwell sec": round(float(zone.get("avg_dwell_ms", 0.0)) / 1000, 1),
                "Heat score": zone.get("heat_score", 0),
                "Confidence": zone.get("data_confidence", "LOW"),
            }
        )
    if not rows:
        for zone_id, dwell in dwell_zones.items():
            rows.append(
                {
                    "Zone": zone_id,
                    "Visits": 0,
                    "Visitors": dwell.get("visitor_count", 0),
                    "Avg dwell sec": round(float(dwell.get("avg_dwell_ms", 0.0)) / 1000, 1),
                    "Heat score": 0,
                    "Confidence": "LOW",
                }
            )
    frame = pd.DataFrame(rows).sort_values(
        ["Heat score", "Avg dwell sec", "Visits"],
        ascending=[False, False, False],
    )
    st.dataframe(frame.head(10), hide_index=True, use_container_width=True)


def render_anomalies(anomalies_payload: dict[str, Any]) -> None:
    anomalies = anomalies_payload.get("anomalies") or []
    st.subheader("Anomaly Panel")
    if not anomalies:
        st.success("No active anomalies.")
        return

    severity_order = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
    for anomaly in sorted(anomalies, key=lambda item: severity_order.get(item.get("severity"), 9)):
        severity = anomaly.get("severity", "INFO")
        label = f"{anomaly.get('type', 'ANOMALY')} - {anomaly.get('timestamp', '')}"
        if severity == "CRITICAL":
            st.error(label)
        elif severity == "WARN":
            st.warning(label)
        else:
            st.info(label)
        details = anomaly.get("details") or {}
        if details:
            st.json(details, expanded=False)


def render_health(health: dict[str, Any], store_id: str) -> None:
    stores = health.get("stores") or []
    selected = next((store for store in stores if store.get("store_id") == store_id), None)
    st.subheader("Feed Health")
    if selected is None:
        st.caption("No events have been ingested for this store yet.")
        return
    status = selected.get("feed_status", "NO_EVENTS")
    lag = selected.get("lag_seconds")
    if status == "OK":
        st.success(f"Feed OK. Lag: {lag}s")
    elif status == "STALE_FEED":
        st.warning(f"Stale feed. Lag: {lag}s")
    else:
        st.info("No events available.")


def main() -> None:
    st.set_page_config(page_title="Store Intelligence Dashboard", layout="wide")
    st.title("Store Intelligence Command Center")
    st.caption("Live operational dashboard powered only by Store Intelligence APIs.")

    with st.sidebar:
        api_url = st.text_input("API URL", DEFAULT_API_URL)
        store_id = st.text_input("Store ID", DEFAULT_STORE_ID)
        refresh_seconds = st.slider("Refresh seconds", min_value=2, max_value=30, value=5)
        auto_refresh = st.toggle("Live refresh", value=True)
        st.caption("No mock data is rendered; empty API datasets show empty states.")

    results = {
        "metrics": fetch_json(api_url, f"/stores/{store_id}/metrics"),
        "funnel": fetch_json(api_url, f"/stores/{store_id}/funnel"),
        "heatmap": fetch_json(api_url, f"/stores/{store_id}/heatmap"),
        "anomalies": fetch_json(api_url, f"/stores/{store_id}/anomalies"),
        "dashboard": fetch_json(api_url, f"/stores/{store_id}/dashboard"),
        "health": fetch_json(api_url, "/health"),
    }

    errors = [result.error for result in results.values() if not result.ok and result.error]
    if errors:
        st.error("API connection or response error.")
        for error in errors:
            st.code(error)
    else:
        metrics = results["metrics"].data or {}
        funnel = results["funnel"].data or {}
        heatmap = results["heatmap"].data or {}
        anomalies = results["anomalies"].data or {}
        summary = results["dashboard"].data or {}
        health = results["health"].data or {}

        render_metric_cards(metrics)
        trend_col, conversion_col = st.columns(2)
        with trend_col:
            render_visitor_trend(summary)
        with conversion_col:
            render_conversion_chart(summary)

        dwell_col, funnel_col = st.columns([1.1, 1])
        with dwell_col:
            render_zone_dwell_chart(metrics)
        with funnel_col:
            render_funnel(funnel)

        queue_col, zones_col = st.columns([0.9, 1.1])
        with queue_col:
            render_queue_chart(metrics)
        with zones_col:
            render_top_zones(metrics, heatmap)

        anomaly_col, health_col = st.columns([1.3, 0.7])
        with anomaly_col:
            render_anomalies(anomalies)
        with health_col:
            render_health(health, store_id)

    st.caption(f"Last refresh: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if auto_refresh:
        time.sleep(refresh_seconds)
        st.rerun()


if __name__ == "__main__":
    main()
