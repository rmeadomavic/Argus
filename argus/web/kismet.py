"""Argus — Kismet REST API client with session caching and response caching."""

from __future__ import annotations

import json
import logging
import time
import asyncio
from typing import Any

import requests
from fastapi import HTTPException

log = logging.getLogger(__name__)

KISMET_URL = "http://localhost:2501"
KISMET_USER = "kismet"
KISMET_PASS = "kismet"

# Session cache — reuse HTTP session/client for 60 seconds
_session_cache: requests.Session | None = None
_async_session_cache: requests.Session | None = None
_session_time: float = 0
_async_session_time: float = 0

# Response cache — returns stale data on Kismet timeout instead of 503
_response_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 30  # seconds


def _policy_for(endpoint: str) -> tuple[float, int]:
    """Return (timeout, retries) policy by endpoint type."""
    if endpoint.startswith("/system/status"):
        return 3.0, 0
    if endpoint.startswith("/devices/") or endpoint.startswith("/gps/"):
        return 8.0, 1
    if endpoint.startswith("/datasource/") or endpoint.startswith("/logging/"):
        return 12.0, 1
    # Export and large data endpoints can run longer
    if endpoint.startswith("/phy/") or endpoint.startswith("/alerts/"):
        return 20.0, 2
    return 8.0, 1


def session() -> requests.Session:
    """Get or create a cached Kismet HTTP session with auth cookies."""
    global _session_cache, _session_time
    if _session_cache and (time.time() - _session_time) < 60:
        return _session_cache
    s = requests.Session()
    s.auth = (KISMET_USER, KISMET_PASS)
    s.headers.update({"Accept": "application/json"})
    try:
        r = s.get(f"{KISMET_URL}/session/check_session", timeout=3)
        if r.status_code == 200 and "KISMET" in r.cookies:
            s.cookies.update(r.cookies)
    except (requests.ConnectionError, requests.Timeout):
        pass
    _session_cache = s
    _session_time = time.time()
    return s


async def async_session() -> requests.Session:
    """Get or create a cached async-compatible Kismet HTTP session with auth cookies."""
    global _async_session_cache, _async_session_time
    if _async_session_cache and (time.time() - _async_session_time) < 60:
        return _async_session_cache

    client = requests.Session()
    client.auth = (KISMET_USER, KISMET_PASS)
    client.headers.update({"Accept": "application/json"})
    try:
        loop = asyncio.get_running_loop()
        r = await loop.run_in_executor(None, lambda: client.get(f"{KISMET_URL}/session/check_session", timeout=3))
        if r.status_code == 200 and "KISMET" in r.cookies:
            client.cookies.update(r.cookies)
    except (requests.ConnectionError, requests.Timeout):
        pass

    _async_session_cache = client
    _async_session_time = time.time()
    return client


async def get_async(endpoint: str, params: dict | None = None, timeout: float | None = None) -> Any:
    """Async GET from Kismet with caching fallback on connection/timeout errors."""
    default_timeout, retries = _policy_for(endpoint)
    request_timeout = timeout if timeout is not None else default_timeout
    cache_key = f"GET:{endpoint}:{params}"

    client = await async_session()
    loop = asyncio.get_running_loop()
    for attempt in range(retries + 1):
        try:
            r = await loop.run_in_executor(
                None,
                lambda: client.get(f"{KISMET_URL}{endpoint}", params=params, timeout=request_timeout),
            )
            r.raise_for_status()
            result = r.json()
            _response_cache[cache_key] = (time.time(), result)
            return result
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < retries:
                continue
            if cache_key in _response_cache:
                cached_time, cached_data = _response_cache[cache_key]
                if time.time() - cached_time < _CACHE_TTL:
                    log.warning("Kismet GET %s failed, serving %ds-old cache", endpoint, int(time.time() - cached_time))
                    return cached_data
            if isinstance(e, requests.ConnectionError):
                raise HTTPException(status_code=502, detail="Kismet not reachable")
            raise HTTPException(status_code=504, detail="Kismet request timed out")
        except requests.HTTPError as e:
            raise HTTPException(status_code=e.response.status_code if e.response else 500, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


async def post_async(endpoint: str, data: dict | None = None, timeout: float | None = None) -> Any:
    """Async POST to Kismet (form-encoded) with caching fallback on errors."""
    default_timeout, retries = _policy_for(endpoint)
    request_timeout = timeout if timeout is not None else default_timeout
    cache_key = f"POST:{endpoint}:{_cacheable_payload(data)}"

    client = await async_session()
    loop = asyncio.get_running_loop()
    for attempt in range(retries + 1):
        try:
            r = await loop.run_in_executor(
                None,
                lambda: client.post(f"{KISMET_URL}{endpoint}", data=data, timeout=request_timeout),
            )
            r.raise_for_status()
            result = r.json()
            _response_cache[cache_key] = (time.time(), result)
            return result
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < retries:
                continue
            if cache_key in _response_cache:
                cached_time, cached_data = _response_cache[cache_key]
                if time.time() - cached_time < _CACHE_TTL:
                    log.warning("Kismet POST %s failed, serving %ds-old cache", endpoint, int(time.time() - cached_time))
                    return cached_data
            if isinstance(e, requests.ConnectionError):
                raise HTTPException(status_code=502, detail="Kismet not reachable")
            raise HTTPException(status_code=504, detail="Kismet request timed out")
        except requests.HTTPError as e:
            raise HTTPException(status_code=e.response.status_code if e.response else 500, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


def get(endpoint: str, params: dict | None = None, timeout: int = 8) -> Any:
    """GET from Kismet with caching fallback on connection/timeout errors only."""
    cache_key = f"GET:{endpoint}:{params}"
    s = session()
    try:
        r = s.get(f"{KISMET_URL}{endpoint}", params=params, timeout=timeout)
        r.raise_for_status()
        result = r.json()
        _response_cache[cache_key] = (time.time(), result)
        return result
    except (requests.ConnectionError, requests.Timeout) as e:
        # Network-level failures — serve stale cache if available
        if cache_key in _response_cache:
            cached_time, cached_data = _response_cache[cache_key]
            if time.time() - cached_time < _CACHE_TTL:
                log.warning("Kismet GET %s failed, serving %ds-old cache", endpoint, int(time.time() - cached_time))
                return cached_data
        if isinstance(e, requests.ConnectionError):
            raise HTTPException(status_code=502, detail="Kismet not reachable")
        raise HTTPException(status_code=504, detail="Kismet request timed out")
    except requests.HTTPError as e:
        # HTTP-level errors (401, 403, 500) — do NOT serve stale cache
        raise HTTPException(status_code=e.response.status_code if e.response else 500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _cacheable_payload(data: dict | None) -> str:
    """Build a stable string key for POST payloads."""
    if not data:
        return ""
    try:
        return json.dumps(data, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(data)


def post(endpoint: str, data: dict | None = None, timeout: int = 15) -> Any:
    """POST to Kismet (form-encoded) with caching fallback on errors."""
    cache_key = f"POST:{endpoint}:{_cacheable_payload(data)}"
    s = session()
    try:
        r = s.post(f"{KISMET_URL}{endpoint}", data=data, timeout=timeout)
        r.raise_for_status()
        result = r.json()
        _response_cache[cache_key] = (time.time(), result)
        return result
    except (requests.ConnectionError, requests.Timeout) as e:
        if cache_key in _response_cache:
            cached_time, cached_data = _response_cache[cache_key]
            if time.time() - cached_time < _CACHE_TTL:
                log.warning("Kismet POST %s failed, serving %ds-old cache", endpoint, int(time.time() - cached_time))
                return cached_data
        if isinstance(e, requests.ConnectionError):
            raise HTTPException(status_code=502, detail="Kismet not reachable")
        raise HTTPException(status_code=504, detail="Kismet request timed out")
    except requests.HTTPError as e:
        raise HTTPException(status_code=e.response.status_code if e.response else 500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def check_online_async() -> tuple[bool, int]:
    """Check if Kismet is reachable (async). Returns (online, device_count)."""
    try:
        data = await get_async("/system/status.json")
        if isinstance(data, dict):
            return True, data.get("kismet.system.devices.count", 0)
    except HTTPException:
        pass
    return False, 0


def check_online() -> tuple[bool, int]:
    """Check if Kismet is reachable. Returns (online, device_count)."""
    try:
        r = requests.get(
            f"{KISMET_URL}/system/status.json",
            auth=(KISMET_USER, KISMET_PASS), timeout=3,
        )
        if r.status_code == 200:
            data = r.json()
            return True, data.get("kismet.system.devices.count", 0)
    except Exception:
        pass
    return False, 0
