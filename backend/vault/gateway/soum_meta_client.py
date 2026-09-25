"""Gateway → metadata calls with error pass-through, gateway state, config polling. Task S5.
Owner: Soum.

meta(...) returns the parsed JSON body or raises VaultHTTPError:
  * metadata answered with an error → the same status/code/message/detail (e.g. 404 no_bucket,
    503 not_enough_machines, 412 precondition_failed), unless remapped by the caller;
  * metadata unreachable (NetworkError) → 503 metadata_unavailable.
"""
import asyncio
import logging
import time
from typing import Any, Optional

from vault.common.config import SafetyCfg, VaultConfig
from vault.common.rpc import NetworkError, get_rpc
from vault.common.service import VaultHTTPError
from vault.gateway.soum_stats import Stats
from vault.node.soum_heartbeat import parse_config

log = logging.getLogger("gateway")


class Gateway:
    """Per-process gateway state (app.state.gw)."""

    def __init__(self, cfg: VaultConfig, pid: str = "gw"):
        self.cfg = cfg
        self.pid = pid
        self.safety: SafetyCfg = cfg.safety
        self.config_version = -1
        self.app_state = None
        self.stats = Stats()


async def meta(method: str, path: str, *, json: Any = None, params: Optional[dict[str, Any]] = None,
               timeout: Optional[float] = None, ok: tuple[int, ...] = (200, 201)) -> Any:
    try:
        r = await get_rpc().request("meta", method, path, json=json, params=params, timeout=timeout)
    except NetworkError as e:
        raise VaultHTTPError(503, "metadata_unavailable",
                             "Vault's index is restarting. Try again in a moment.", {"reason": e.reason}) from None
    if r.status_code in ok:
        return r.json() if r.content else None
    try:
        body = r.json()
        raise VaultHTTPError(r.status_code, body.get("error", "metadata_error"), body.get("message", ""),
                             body.get("detail") or {})
    except ValueError:
        raise VaultHTTPError(502, "metadata_error", f"Vault's index answered HTTP {r.status_code}.") from None


async def poll_config(gw: Gateway) -> None:
    """§4.15: the gateway polls GET /v1/config every 1 s and applies safety switches on a version change."""
    while True:
        started = time.monotonic()
        try:
            r = await get_rpc().request("meta", "GET", "/v1/config")
            if r.status_code == 200:
                body = r.json()
                if int(body.get("config_version", 0)) != gw.config_version:
                    cfg, safety, version = parse_config(body)
                    if cfg is not None:
                        gw.cfg = cfg
                    gw.safety, gw.config_version = safety, version
                    if gw.app_state is not None:
                        gw.app_state.config_version = version
                    get_rpc().relay_enabled = safety.relay
                    log.info("config v%d applied (safety off: %s)", version,
                             ",".join(k for k, on in safety.model_dump().items() if not on) or "none")
        except NetworkError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("config poll failed")
        await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - started)))
