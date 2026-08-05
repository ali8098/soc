"""SocTalk per-tenant adapter — Zeek + Suricata log ingestion to L1.

Follows the same pattern as the Wazuh adapter (soctalk_adapter/main.py):
- Tail log files from disk (Zeek conn.log / Suricata eve.json)
- Normalize to SocTalk's source event shape
- POST batches to /api/internal/adapter/events
- Heartbeat + checkpoint for resumable ingest

Environment variables:
  SOCTALK_API_URL            — L1 API base URL (required)
  SOCTALK_TENANT_ID          — tenant UUID (required)
  ADAPTER_TOKEN_PATH         — path to bearer token file
                               (default /run/secrets/adapter/token)
  SOCTALK_API_VERIFY_SSL     — verify L1 TLS (default true)

  ZEEK_ENABLED               — enable Zeek ingest (default 0)
  ZEEK_LOG_PATH              — path to Zeek conn.log or log dir
                               (default /var/log/zeek/current/conn.log)
  ZEEK_LOG_FORMAT            — "json" or "tsv" (default "json")

  SURICATA_ENABLED           — enable Suricata ingest (default 0)
  SURICATA_LOG_PATH          — path to Suricata eve.json
                               (default /var/log/suricata/eve.json)
  SURICATA_INGEST_ALL_EVENTS — ingest non-alert EVE events too (default 0)

  SOCTALK_INGEST_INTERVAL_SECONDS       — poll interval (default 15)
  SOCTALK_INGEST_BATCH_SIZE             — events per POST (default 100)
  SOCTALK_ADAPTER_MIN_SEVERITY          — min severity 0-15 (default 3)
  SOCTALK_HEARTBEAT_INTERVAL_SECONDS    — heartbeat interval (default 30)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from soctalk_wire import (
    REDACTION_VERSION,
    SCHEMA_VERSION,
    TEMPLATE_VERSION,
    redact_text,
    template_hash,
)

logger = logging.getLogger("soctalk.adapter.nids")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

VERSION = "0.1.0"

CHECKPOINT_LOAD_MAX_ATTEMPTS = 10
CHECKPOINT_LOAD_RETRY_SECONDS = 6.0


# ---------------------------------------------------------------------------
# Token + config helpers
# ---------------------------------------------------------------------------

def _read_token() -> str:
    path = Path(os.environ.get("ADAPTER_TOKEN_PATH", "/run/secrets/adapter/token"))
    return path.read_text().strip()


def _soctalk_api_verify_ssl() -> bool:
    raw = os.environ.get("SOCTALK_API_VERIFY_SSL", "true")
    normalized = raw.strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    logger.warning("SOCTALK_API_VERIFY_SSL=%r unrecognised — defaulting to True", raw)
    return True


def _min_severity() -> int:
    try:
        return max(0, min(15, int(os.environ.get("SOCTALK_ADAPTER_MIN_SEVERITY", "3"))))
    except ValueError:
        return 3


# ---------------------------------------------------------------------------
# State — one per source
# ---------------------------------------------------------------------------

class _SourceState:
    def __init__(self, source: str) -> None:
        self.source = source
        self.file_offset: int = 0
        self.last_ts: str = "1970-01-01T00:00:00.000Z"
        self.events_queried: int = 0
        self.events_forwarded: int = 0
        self.events_duplicate: int = 0
        self.batch_seq: int = 0
        self.last_error: str | None = None
        self.checkpoint_loaded: bool = False


_zeek_state = _SourceState("zeek")
_suricata_state = _SourceState("suricata")


# ---------------------------------------------------------------------------
# IOC + routing helpers
# ---------------------------------------------------------------------------

_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
_MD5_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|ru|cn|tk|xyz|info|biz)\b",
    re.IGNORECASE,
)


def _is_routable_ip(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b, *_ = (int(p) for p in parts)
    except ValueError:
        return False
    if a in (10, 127, 0):
        return False
    if a == 172 and 16 <= b <= 31:
        return False
    if a == 192 and b == 168:
        return False
    if a == 169 and b == 254:
        return False
    return True


def _extract_iocs(text: str) -> list[dict]:
    if not text:
        return []
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for ip in _IPV4_RE.findall(text):
        if not _is_routable_ip(ip):
            continue
        key = ("ip", ip)
        if key not in seen:
            seen.add(key)
            out.append({"type": "ip", "value": ip})
    for h in _SHA256_RE.findall(text):
        key = ("hash_sha256", h.lower())
        if key not in seen:
            seen.add(key)
            out.append({"type": "hash_sha256", "value": h.lower()})
    for h in _MD5_RE.findall(text):
        key = ("hash_md5", h.lower())
        if key not in seen:
            seen.add(key)
            out.append({"type": "hash_md5", "value": h.lower()})
    for d in _DOMAIN_RE.findall(text):
        key = ("domain", d.lower())
        if key not in seen:
            seen.add(key)
            out.append({"type": "domain", "value": d.lower()})
    return out[:32]


# ---------------------------------------------------------------------------
# Zeek normalization — JSON + TSV
# ---------------------------------------------------------------------------

_ZEEK_CONN_STATE_SEVERITY: dict[str, int] = {
    "S0": 6,    # SYN sent, no response — possible scan
    "REJ": 5,   # Connection rejected
    "RSTOS0": 5,
    "RSTRH": 5,
    "SH": 4,
    "SHR": 4,
    "OTH": 3,
    "SF": 2,    # Normal established connection
    "S1": 2,
    "S2": 2,
    "S3": 2,
    "RSTO": 3,
    "RSTR": 3,
}

# Standard Zeek conn.log TSV field order (zeek 4.x / 5.x / 6.x).
# The #fields header line is authoritative when present; this is the
# fallback for files that omit it (e.g. rotated/partial logs).
_ZEEK_CONN_TSV_FIELDS = [
    "ts", "uid", "id.orig_h", "id.orig_p", "id.resp_h", "id.resp_p",
    "proto", "service", "duration", "orig_bytes", "resp_bytes",
    "conn_state", "local_orig", "local_resp", "missed_bytes",
    "history", "orig_pkts", "orig_ip_bytes", "resp_pkts",
    "resp_ip_bytes", "tunnel_parents",
]


def _parse_zeek_tsv_line(line: str, fields: list[str]) -> dict | None:
    """Parse one Zeek TSV data line into a dict using the provided field list.

    Returns None for comment/header lines (start with '#') or if the
    column count doesn't match. Missing trailing columns are filled with
    '-' (Zeek's null sentinel).
    """
    if line.startswith("#"):
        return None
    parts = line.split("\t")
    if not parts:
        return None
    # Pad short rows (trailing optional columns may be absent)
    while len(parts) < len(fields):
        parts.append("-")
    rec: dict[str, str] = {}
    for i, f in enumerate(fields):
        v = parts[i] if i < len(parts) else "-"
        rec[f] = "" if v == "-" else v
    return rec


def _zeek_tsv_to_event(line: str, fields: list[str]) -> dict | None:
    """Parse one Zeek TSV log line into a SocTalk source event."""
    rec = _parse_zeek_tsv_line(line, fields)
    if rec is None:
        return None

    ts_raw = rec.get("ts")
    if not ts_raw:
        return None
    try:
        ts_dt = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
        ts_iso = ts_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except (ValueError, TypeError, OSError):
        return None

    uid = rec.get("uid") or ""
    src_ip = rec.get("id.orig_h") or ""
    dst_ip = rec.get("id.resp_h") or ""
    src_port = rec.get("id.orig_p") or ""
    dst_port = rec.get("id.resp_p") or ""
    proto = rec.get("proto") or ""
    service = rec.get("service") or ""
    conn_state = rec.get("conn_state") or ""

    severity = _ZEEK_CONN_STATE_SEVERITY.get(conn_state, 2)
    if severity < _min_severity():
        return None

    return _build_zeek_event(
        ts_iso=ts_iso,
        uid=uid,
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=src_port,
        dst_port=dst_port,
        proto=proto,
        service=service,
        conn_state=conn_state,
        severity=severity,
        log_type="conn",
        raw_text="\t".join(rec.get(f, "-") for f in fields),
    )


def _zeek_json_to_event(line: str) -> dict | None:
    """Parse one Zeek JSON log line into a SocTalk source event."""
    try:
        rec = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    log_type = rec.get("_path") or rec.get("path") or "conn"
    ts_raw = rec.get("ts")
    if ts_raw is None:
        return None
    try:
        ts_dt = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
        ts_iso = ts_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except (ValueError, TypeError, OSError):
        return None

    uid = rec.get("uid") or ""
    src_ip = rec.get("id.orig_h") or rec.get("orig_h") or ""
    dst_ip = rec.get("id.resp_h") or rec.get("resp_h") or ""
    src_port = rec.get("id.orig_p") or rec.get("orig_p") or ""
    dst_port = rec.get("id.resp_p") or rec.get("resp_p") or ""
    proto = rec.get("proto") or ""
    service = rec.get("service") or ""
    conn_state = rec.get("conn_state") or ""

    severity = _ZEEK_CONN_STATE_SEVERITY.get(conn_state, 2)
    if severity < _min_severity():
        return None

    return _build_zeek_event(
        ts_iso=ts_iso,
        uid=uid,
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=src_port,
        dst_port=dst_port,
        proto=proto,
        service=service,
        conn_state=conn_state,
        severity=severity,
        log_type=log_type,
        raw_text=json.dumps(rec),
    )


def _build_zeek_event(
    *,
    ts_iso: str,
    uid: str,
    src_ip: str,
    dst_ip: str,
    src_port: str | int,
    dst_port: str | int,
    proto: str,
    service: str,
    conn_state: str,
    severity: int,
    log_type: str,
    raw_text: str,
) -> dict:
    """Shared event-shape builder for both Zeek JSON and TSV parsers."""
    title_parts = [f"Zeek {log_type}"]
    if src_ip and dst_ip:
        title_parts.append(f"{src_ip}:{src_port} → {dst_ip}:{dst_port}")
    if service:
        title_parts.append(f"[{service}]")
    if conn_state:
        title_parts.append(f"state={conn_state}")
    title = redact_text(" ".join(title_parts))[:255]

    iocs = _extract_iocs(raw_text)
    description = redact_text(f"Zeek {log_type}: {raw_text}")[:1024]

    entities: list[dict] = []
    if src_ip and _is_routable_ip(src_ip):
        entities.append({"type": "ip", "value": src_ip, "role": "src", "source_field": "id.orig_h"})
    if dst_ip and _is_routable_ip(dst_ip):
        entities.append({"type": "ip", "value": dst_ip, "role": "dst", "source_field": "id.resp_h"})
    if src_port:
        entities.append({"type": "port", "value": str(src_port), "role": "src", "source_field": "id.orig_p"})
    if dst_port:
        entities.append({"type": "port", "value": str(dst_port), "role": "dst", "source_field": "id.resp_p"})

    full_log_red = redact_text(raw_text)[:4096]
    thash = template_hash(full_log_red)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    return {
        "source_event_id": f"zeek-{uid or ts_iso}",
        "source": "zeek",
        "rule_id": log_type,
        "severity": severity,
        "asset_ids": [],
        "initial_iocs": iocs,
        "ts": ts_iso,
        "observed_at": now_iso,
        "title": title,
        "description": description,
        "entities": entities[:64],
        "mitre": {},
        "rule_groups": [f"zeek_{log_type}", f"proto_{proto}"] if proto else [f"zeek_{log_type}"],
        "decoder": "zeek_tsv" if "\t" in raw_text else "zeek_json",
        "full_log": full_log_red,
        "template_hash": thash,
        "template_version": TEMPLATE_VERSION,
        "redaction_version": REDACTION_VERSION,
        "raw": {
            "log_type": log_type,
            "proto": proto,
            "service": service,
            "conn_state": conn_state,
            "src": f"{src_ip}:{src_port}",
            "dst": f"{dst_ip}:{dst_port}",
        },
    }


# ---------------------------------------------------------------------------
# Suricata normalization
# ---------------------------------------------------------------------------

_SURICATA_SEVERITY_MAP: dict[int, int] = {
    1: 12,  # high
    2: 8,   # medium
    3: 4,   # low
}


def _suricata_eve_to_event(line: str) -> dict | None:
    """Parse one Suricata EVE JSON line into a SocTalk source event."""
    try:
        rec = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    event_type = rec.get("event_type") or ""

    # Only ingest alert events by default — dns/flow/http/tls are high-volume
    # background noise. Set SURICATA_INGEST_ALL_EVENTS=1 to override.
    if event_type != "alert" and not os.environ.get("SURICATA_INGEST_ALL_EVENTS"):
        return None

    ts_raw = rec.get("timestamp")
    if not ts_raw:
        return None
    ts_iso = str(ts_raw).replace(" ", "T")
    if not ts_iso.endswith("Z"):
        ts_iso = ts_iso[:26] + "Z"

    src_ip = rec.get("src_ip") or ""
    dst_ip = rec.get("dest_ip") or ""
    src_port = rec.get("src_port")
    dst_port = rec.get("dest_port")
    proto = rec.get("proto") or ""
    alert = rec.get("alert") or {}
    signature = alert.get("signature") or ""
    signature_id = alert.get("signature_id") or ""
    category = alert.get("category") or ""
    suricata_sev = int(alert.get("severity") or 3)
    severity = _SURICATA_SEVERITY_MAP.get(suricata_sev, 4)

    if severity < _min_severity():
        return None

    mitre: dict[str, Any] = {}
    metadata = alert.get("metadata") or {}
    if isinstance(metadata, dict):
        techniques = metadata.get("mitre_technique_id") or []
        tactics = metadata.get("mitre_tactic_id") or []
        if techniques or tactics:
            mitre = {
                "techniques": [str(t) for t in techniques][:16],
                "tactics": [str(t) for t in tactics][:16],
                "ids": [],
            }

    title_parts = [signature or f"Suricata {event_type}"]
    if src_ip and dst_ip:
        title_parts.append(f"{src_ip}:{src_port} → {dst_ip}:{dst_port}")
    title = redact_text(" ".join(title_parts))[:255]

    raw_text = json.dumps(rec)
    iocs = _extract_iocs(raw_text)
    description = redact_text(f"Suricata {event_type}: {signature} — {raw_text}")[:1024]

    entities: list[dict] = []
    if src_ip and _is_routable_ip(src_ip):
        entities.append({"type": "ip", "value": src_ip, "role": "src", "source_field": "src_ip"})
    if dst_ip and _is_routable_ip(dst_ip):
        entities.append({"type": "ip", "value": dst_ip, "role": "dst", "source_field": "dest_ip"})
    if src_port:
        entities.append({"type": "port", "value": str(src_port), "role": "src", "source_field": "src_port"})
    if dst_port:
        entities.append({"type": "port", "value": str(dst_port), "role": "dst", "source_field": "dest_port"})

    flow_id = str(rec.get("flow_id") or "")
    full_log_red = redact_text(raw_text)[:4096]
    thash = template_hash(full_log_red)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    return {
        "source_event_id": f"suricata-{flow_id or ts_iso}-{signature_id}",
        "source": "suricata",
        "rule_id": str(signature_id),
        "severity": severity,
        "asset_ids": [],
        "initial_iocs": iocs,
        "ts": ts_iso,
        "observed_at": now_iso,
        "title": title,
        "description": description,
        "entities": entities[:64],
        "mitre": mitre,
        "rule_groups": [
            f"suricata_{event_type}",
            f"category_{category.lower().replace(' ', '_')}",
        ] if category else [f"suricata_{event_type}"],
        "decoder": "suricata_eve",
        "full_log": full_log_red,
        "template_hash": thash,
        "template_version": TEMPLATE_VERSION,
        "redaction_version": REDACTION_VERSION,
        "raw": {
            "event_type": event_type,
            "signature": signature,
            "signature_id": signature_id,
            "category": category,
            "proto": proto,
            "src": f"{src_ip}:{src_port}",
            "dst": f"{dst_ip}:{dst_port}",
            "severity": suricata_sev,
        },
    }


# ---------------------------------------------------------------------------
# File tail (offset-based, rotation-safe)
# ---------------------------------------------------------------------------

def _read_new_lines(log_path: Path, state: _SourceState, batch_size: int) -> list[str]:
    """Read new lines from log file starting at state.file_offset.

    Rotation-safe: if file is shorter than our offset (rotated/truncated),
    reset offset to 0 and read from start.
    """
    if not log_path.exists():
        return []
    try:
        size = log_path.stat().st_size
        if size < state.file_offset:
            logger.info("%s log rotated/truncated, resetting offset", state.source)
            state.file_offset = 0
        if size == state.file_offset:
            return []
        lines: list[str] = []
        with log_path.open("rb") as f:
            f.seek(state.file_offset)
            while len(lines) < batch_size:
                raw = f.readline()
                if not raw:
                    break
                state.file_offset = f.tell()
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    lines.append(line)
        return lines
    except OSError as e:
        logger.warning("%s log read error: %s", state.source, e)
        return []


# ---------------------------------------------------------------------------
# Zeek TSV field-header scanning
# ---------------------------------------------------------------------------

def _scan_zeek_tsv_fields(log_path: Path) -> list[str]:
    """Read the Zeek TSV #fields header from the log file.

    Zeek TSV logs start with comment lines:
      #separator \\x09
      #fields  ts  uid  id.orig_h  ...
      #types   time  string  addr  ...

    We scan the first 50 lines to find '#fields'. If not found (e.g. a
    partially rotated file that lost its header), fall back to the
    standard conn.log column order defined in _ZEEK_CONN_TSV_FIELDS.
    """
    try:
        with log_path.open("rb") as f:
            for _ in range(50):
                raw = f.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if line.startswith("#fields"):
                    # '#fields\tts\tuid\tid.orig_h\t...'
                    parts = line.split("\t")
                    fields = [p.strip() for p in parts[1:] if p.strip()]
                    if fields:
                        logger.info("zeek tsv fields from header: %s", fields)
                        return fields
    except OSError as e:
        logger.warning("zeek tsv header scan error: %s", e)
    logger.info("zeek tsv: no #fields header found, using default conn.log columns")
    return _ZEEK_CONN_TSV_FIELDS


# ---------------------------------------------------------------------------
# Checkpoint save/load
# ---------------------------------------------------------------------------

async def _load_checkpoint(
    client: httpx.AsyncClient, api_url: str, token: str,
    source: str, state: _SourceState,
) -> None:
    try:
        resp = await client.get(
            f"{api_url}/api/internal/adapter/checkpoint?source={source}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        cp = resp.json()
        if cp.get("cursor_ts"):
            state.last_ts = cp["cursor_ts"]
        state.batch_seq = int(cp.get("batch_seq") or 0)
        state.checkpoint_loaded = True
        offset_str = cp.get("cursor_event_id") or "0"
        try:
            state.file_offset = int(offset_str)
        except ValueError:
            state.file_offset = 0
        logger.info(
            "%s checkpoint loaded offset=%d ts=%s",
            source, state.file_offset, state.last_ts,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("%s checkpoint load failed: %s", source, e)


async def _save_checkpoint(
    client: httpx.AsyncClient, api_url: str, token: str,
    source: str, state: _SourceState,
) -> None:
    try:
        resp = await client.put(
            f"{api_url}/api/internal/adapter/checkpoint",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "tenant_id": os.environ["SOCTALK_TENANT_ID"],
                "source": source,
                "cursor_ts": state.last_ts,
                "cursor_event_id": str(state.file_offset),
                "batch_seq": state.batch_seq,
                "dropped_total": 0,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        logger.warning("%s checkpoint save failed: %s", source, e)


# ---------------------------------------------------------------------------
# Generic ingest loop (shared by Zeek + Suricata)
# ---------------------------------------------------------------------------

async def _ingest_source(
    api_client: httpx.AsyncClient,
    api_url: str,
    state: _SourceState,
    log_path: Path,
    parser,          # callable: str -> dict | None
    batch_size: int,
    interval: float,
) -> None:
    token = _read_token()

    # Durable checkpoint resume: retry so a token renewed just after pod
    # start is picked up before we ingest from a stale local cursor.
    for _attempt in range(CHECKPOINT_LOAD_MAX_ATTEMPTS):
        await _load_checkpoint(api_client, api_url, token, state.source, state)
        if state.checkpoint_loaded:
            break
        await asyncio.sleep(CHECKPOINT_LOAD_RETRY_SECONDS)
    else:
        logger.warning(
            "%s checkpoint never loaded after %d attempts — starting from offset 0",
            state.source, CHECKPOINT_LOAD_MAX_ATTEMPTS,
        )

    while True:
        token = _read_token()
        try:
            lines = _read_new_lines(log_path, state, batch_size)
            state.events_queried += len(lines)

            events: list[dict] = []
            for line in lines:
                ev = parser(line)
                if ev is not None:
                    events.append(ev)

            if events:
                state.batch_seq += 1
                resp = await api_client.post(
                    f"{api_url}/api/internal/adapter/events",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "tenant_id": os.environ["SOCTALK_TENANT_ID"],
                        "events": events,
                        "schema_version": SCHEMA_VERSION,
                        "batch_seq": state.batch_seq,
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()
                body = resp.json()
                dup = (body.get("action_counts") or {}).get("duplicate", 0)
                state.events_duplicate += dup
                state.events_forwarded += len(events) - dup
                state.last_error = None

                last_ts = events[-1].get("ts")
                if last_ts:
                    state.last_ts = last_ts

                await _save_checkpoint(api_client, api_url, token, state.source, state)
                logger.info(
                    "%s ingest_ok forwarded=%d duplicate=%d offset=%d",
                    state.source, state.events_forwarded,
                    state.events_duplicate, state.file_offset,
                )

        except Exception as e:  # noqa: BLE001
            state.last_error = str(e)
            logger.warning("%s ingest_failed: %s", state.source, e)

        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

async def _heartbeat_loop() -> None:
    api_url = os.environ["SOCTALK_API_URL"].rstrip("/")
    tenant_id = os.environ["SOCTALK_TENANT_ID"]
    interval = float(os.environ.get("SOCTALK_HEARTBEAT_INTERVAL_SECONDS", "30"))
    verify_ssl = _soctalk_api_verify_ssl()

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        while True:
            try:
                token = _read_token()
                metrics = {
                    "zeek_forwarded": _zeek_state.events_forwarded,
                    "zeek_duplicate": _zeek_state.events_duplicate,
                    "zeek_error": _zeek_state.last_error,
                    "suricata_forwarded": _suricata_state.events_forwarded,
                    "suricata_duplicate": _suricata_state.events_duplicate,
                    "suricata_error": _suricata_state.last_error,
                }
                resp = await client.post(
                    f"{api_url}/api/internal/adapter/heartbeat",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "tenant_id": tenant_id,
                        "version": VERSION,
                        "health": "ok",
                        "metrics": metrics,
                    },
                    timeout=10.0,
                )
                resp.raise_for_status()
                logger.info("heartbeat_ok")
            except Exception as e:  # noqa: BLE001
                logger.warning("heartbeat_failed: %s", e)
            await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# FastAPI lifespan — spawn tasks for enabled sources
# ---------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    del app
    api_url = os.environ["SOCTALK_API_URL"].rstrip("/")
    interval = float(os.environ.get("SOCTALK_INGEST_INTERVAL_SECONDS", "15"))
    batch_size = int(os.environ.get("SOCTALK_INGEST_BATCH_SIZE", "100"))
    verify_ssl = _soctalk_api_verify_ssl()

    tasks: list[asyncio.Task] = []
    tasks.append(asyncio.create_task(_heartbeat_loop(), name="nids-heartbeat"))

    async with httpx.AsyncClient(verify=verify_ssl) as api_client:
        # --- Zeek ---
        zeek_enabled = os.environ.get("ZEEK_ENABLED", "0") in {"1", "true"}
        if zeek_enabled:
            zeek_path = Path(
                os.environ.get("ZEEK_LOG_PATH", "/var/log/zeek/current/conn.log")
            )
            zeek_fmt = os.environ.get("ZEEK_LOG_FORMAT", "json").strip().lower()
            logger.info("zeek ingest enabled path=%s format=%s", zeek_path, zeek_fmt)

            if zeek_fmt == "tsv":
                # Scan the #fields header once at startup so the parser
                # closure captures the correct column order. If the file
                # doesn't exist yet (e.g. Zeek not started), we get the
                # default fallback — _read_new_lines will simply return []
                # until the file appears.
                tsv_fields = _scan_zeek_tsv_fields(zeek_path)

                def _zeek_parser(line: str, _f: list[str] = tsv_fields) -> dict | None:
                    return _zeek_tsv_to_event(line, _f)
            else:
                _zeek_parser = _zeek_json_to_event  # type: ignore[assignment]

            tasks.append(asyncio.create_task(
                _ingest_source(
                    api_client, api_url, _zeek_state,
                    zeek_path, _zeek_parser,
                    batch_size, interval,
                ),
                name="zeek-ingest",
            ))

        # --- Suricata ---
        suricata_enabled = os.environ.get("SURICATA_ENABLED", "0") in {"1", "true"}
        if suricata_enabled:
            suricata_path = Path(
                os.environ.get("SURICATA_LOG_PATH", "/var/log/suricata/eve.json")
            )
            logger.info("suricata ingest enabled path=%s", suricata_path)
            tasks.append(asyncio.create_task(
                _ingest_source(
                    api_client, api_url, _suricata_state,
                    suricata_path, _suricata_eve_to_event,
                    batch_size, interval,
                ),
                name="suricata-ingest",
            ))

        if not zeek_enabled and not suricata_enabled:
            logger.warning(
                "neither ZEEK_ENABLED nor SURICATA_ENABLED is set — adapter idle"
            )

        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t


app = FastAPI(lifespan=_lifespan)


@app.get("/health/live")
async def live() -> dict:
    return {"ok": True, "version": VERSION}


@app.get("/health/ready")
async def ready() -> dict:
    return {
        "ok": True,
        "zeek": {
            "forwarded": _zeek_state.events_forwarded,
            "duplicate": _zeek_state.events_duplicate,
            "offset": _zeek_state.file_offset,
            "last_ts": _zeek_state.last_ts,
            "last_error": _zeek_state.last_error,
            "checkpoint_loaded": _zeek_state.checkpoint_loaded,
        },
        "suricata": {
            "forwarded": _suricata_state.events_forwarded,
            "duplicate": _suricata_state.events_duplicate,
            "offset": _suricata_state.file_offset,
            "last_ts": _suricata_state.last_ts,
            "last_error": _suricata_state.last_error,
            "checkpoint_loaded": _suricata_state.checkpoint_loaded,
        },
    }
