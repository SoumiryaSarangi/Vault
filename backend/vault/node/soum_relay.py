"""ANY /v1/relay/{target}/{path}: requires X-Vault-Hop: 1, forwards once via rpc.forward(). §4.7. Task S6.
Owner: Soum.

The caller's rpc picked this node because its own link to `target` is down. We forward once, directly
(never relaying again), with rpc.forward(), which sets X-Vault-From=<us>, X-Vault-Origin=<caller>,
X-Vault-Via=<us>, so the target's netsim judges OUR link to it and metadata sees the route "relay:<us>".

If our own forward fails (target dead or our link to it cut too) we answer 502 target_unreachable, not 599:
a 599 would make the caller's rpc try every other relay in turn, and on Windows each attempt at a dead
machine takes ~2 s to be refused. With 502 the caller gets one answer and moves on (spare, next holder,
next heartbeat).
"""
import logging

from fastapi import APIRouter, Request, Response

from vault.common.rpc import NetworkError, get_rpc
from vault.common.service import VaultHTTPError

log = logging.getLogger("node.relay")

# Headers that describe one hop's connection, not the message (RFC 9110 §7.6.1) + ones httpx recomputes.
_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authenticate",
        "proxy-authorization", "content-length", "content-encoding"}


def relay_router(node) -> APIRouter:
    r = APIRouter()

    @r.api_route("/v1/relay/{target}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "PATCH"])
    async def relay(target: str, path: str, request: Request) -> Response:
        if request.headers.get("x-vault-hop") != "1":
            raise VaultHTTPError(400, "bad_hop", "Relay requests must carry X-Vault-Hop: 1.")
        if not node.safety.relay:                         # Naive mode: no relay routing (§4.15)
            raise VaultHTTPError(503, "relay_disabled", "Relaying is turned off.")
        timeout_s = node.cfg.gateway.timeout_data_s
        origin = request.headers.get("x-vault-from", "")
        body = await request.body()
        try:
            resp = await get_rpc().forward(target, request.method, "/" + path, origin=origin, content=body,
                                           headers=dict(request.headers), params=dict(request.query_params),
                                           timeout=timeout_s)
        except NetworkError as e:
            log.info("relay %s -> %s %s /%s failed: %s", origin, target, request.method, path, e.reason)
            raise VaultHTTPError(502, "target_unreachable", f"Couldn't reach {target} either.",
                                 {"target": target, "via": get_rpc().pid, "reason": e.reason}) from None
        headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP}
        return Response(content=resp.content, status_code=resp.status_code, headers=headers)

    return r
