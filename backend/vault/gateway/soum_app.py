"""Gateway: public object API, :7080 (ARCHITECTURE §4.1–4.3, §7.1). Owner: Soum. Tasks S5, S7.

    PUT    /{bucket}             {"policy":"rep3"}   → 200 {"bucket","policy"} · 409 bucket_exists
    GET    /{bucket}?prefix=&limit=                  → 200 ObjectList
    PUT    /{bucket}/{key:path}  bytes               → 200 PutResult · 411 · 412 · 503 quorum_not_met /
                                                        metadata_unavailable / not_enough_machines
    GET    /{bucket}/{key:path}                      → 200 bytes (+ ETag, X-Vault-Seq, X-Vault-Commit-Seq,
                                                        X-Vault-Read-Path) · 404 not_found · 503 unavailable
    HEAD   /{bucket}/{key:path}                      → 200 headers · 404
    DELETE /{bucket}/{key:path}                      → 200 {"deleted","commit_seq"}
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from vault.common.config import VaultConfig
from vault.common.models import (Bucket, BucketCreate, BucketInfo, CreateBucketRequest, DeleteResult, Manifest,
                                 ObjectList, PutResult)
from vault.common.service import VaultHTTPError, make_app
from vault.gateway import soum_get, soum_put
from vault.gateway.soum_meta_client import Gateway, meta, poll_config

log = logging.getLogger("gateway")


def object_headers(m: Manifest, read_path: Optional[str] = None) -> dict[str, str]:
    h = {"ETag": m.sha256, "X-Vault-Seq": str(m.seq), "X-Vault-Commit-Seq": str(m.commit_seq),
         "Content-Length": str(m.size)}
    if read_path is not None:
        h["X-Vault-Read-Path"] = read_path
    return h


def create_app(cfg: VaultConfig, pid: str = "gw", start_loops: bool = True) -> FastAPI:
    """start_loops=False: routes only (unit tests); no config polling."""
    gw = Gateway(cfg, pid)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks = [asyncio.create_task(poll_config(gw))] if start_loops else []
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = make_app(cfg, pid, lifespan)
    app.state.gw = gw
    gw.app_state = app.state

    # ── buckets ──
    @app.put("/{bucket}")
    async def create_bucket(bucket: str, request: Request) -> BucketInfo:
        raw = await request.body()
        req = CreateBucketRequest.model_validate_json(raw) if raw.strip() else CreateBucketRequest()
        b = Bucket.model_validate(await meta("POST", "/v1/buckets",
                                             json=BucketCreate(name=bucket, policy=req.policy).model_dump()))
        return BucketInfo(bucket=b.name, policy=b.policy.name)

    @app.get("/{bucket}")
    async def list_objects(bucket: str, prefix: str = "", limit: int = 1000) -> ObjectList:
        return ObjectList.model_validate(await meta("GET", f"/v1/objects/{bucket}",
                                                    params={"prefix": prefix, "limit": limit}))

    # ── objects ──
    @app.put("/{bucket}/{key:path}")
    async def put_object(bucket: str, key: str, request: Request) -> Response:
        length = request.headers.get("content-length")
        if length is None or not length.isdigit():
            gw.stats.record("put", False)
            raise VaultHTTPError(411, "length_required", "Content-Length is required.")
        try:
            res: PutResult = await soum_put.put_object(gw, bucket, key, int(length), request.stream())
        except VaultHTTPError as e:
            gw.stats.record("put", e.status in (404, 412))
            raise
        gw.stats.record("put", True)
        return Response(res.model_dump_json(), media_type="application/json",
                        headers={"ETag": res.etag, "X-Vault-Seq": str(res.seq),
                                 "X-Vault-Commit-Seq": str(res.commit_seq)})

    @app.get("/{bucket}/{key:path}")
    async def get_object(bucket: str, key: str) -> Response:
        try:
            m = await soum_get.get_manifest(bucket, key)
        except VaultHTTPError as e:
            gw.stats.record("get", e.status == 404)
            raise
        try:
            body, path = await soum_get.open_object(gw, m)
        except soum_get.Unreadable as e:
            gw.stats.record("get", False)
            raise VaultHTTPError(503, "unavailable", "This file can't be read right now: not enough healthy "
                                 "copies are reachable.", {"reason": str(e)}) from None
        gw.stats.record("get", True)
        return StreamingResponse(body, media_type="application/octet-stream", headers=object_headers(m, path))

    @app.head("/{bucket}/{key:path}")
    async def head_object(bucket: str, key: str) -> Response:
        try:
            m = await soum_get.get_manifest(bucket, key)
        except VaultHTTPError as e:
            if e.status == 404:
                return Response(status_code=404)
            raise
        return Response(status_code=200, headers=object_headers(m))

    @app.delete("/{bucket}/{key:path}")
    async def delete_object(bucket: str, key: str) -> DeleteResult:
        try:
            res = DeleteResult.model_validate(await meta("DELETE", f"/v1/objects/{bucket}/{key}"))
        except VaultHTTPError as e:
            gw.stats.record("delete", e.status == 404)
            raise
        gw.stats.record("delete", True)
        return res

    return app
