"""HMAC helpers for authenticating client devices to the server."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping

CLIENT_ID_HEADER = "X-Client-Id"
TIMESTAMP_HEADER = "X-Timestamp"
SIGNATURE_HEADER = "X-Signature"


def build_signature_payload(method: str, path: str, query_string: str, timestamp: str) -> str:
    return "\n".join(
        (
            method.upper().strip(),
            path.strip(),
            query_string.strip(),
            timestamp.strip(),
        )
    )


def sign_request(*, client_secret: str, method: str, path: str, query_string: str, timestamp: str) -> str:
    payload = build_signature_payload(method, path, query_string, timestamp)
    digest = hmac.new(client_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()


def verify_request_signature(
    *,
    client_secret: str,
    method: str,
    path: str,
    query_string: str,
    timestamp: str,
    provided_signature: str,
) -> bool:
    expected = sign_request(
        client_secret=client_secret,
        method=method,
        path=path,
        query_string=query_string,
        timestamp=timestamp,
    )
    return hmac.compare_digest(expected, provided_signature.strip())


def is_timestamp_fresh(timestamp: str, *, max_skew_seconds: int, now_epoch: int | None = None) -> bool:
    try:
        request_epoch = int(timestamp.strip())
    except (TypeError, ValueError):
        return False

    current_epoch = int(time.time()) if now_epoch is None else int(now_epoch)
    return abs(current_epoch - request_epoch) <= max_skew_seconds


def build_signed_headers(
    *,
    client_id: str,
    client_secret: str,
    method: str,
    path: str,
    query_string: str,
    timestamp: str | None = None,
) -> Mapping[str, str]:
    actual_timestamp = timestamp or str(int(time.time()))
    signature = sign_request(
        client_secret=client_secret,
        method=method,
        path=path,
        query_string=query_string,
        timestamp=actual_timestamp,
    )
    return {
        CLIENT_ID_HEADER: client_id,
        TIMESTAMP_HEADER: actual_timestamp,
        SIGNATURE_HEADER: signature,
    }
