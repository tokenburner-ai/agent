"""Shared API-key auth — vendored from stack/base-stack/dashboard/app/auth.py."""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps

from flask import request, jsonify

logger = logging.getLogger(__name__)

API_KEYS_TABLE = os.environ.get("API_KEYS_TABLE", "tokenburner-api-keys")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

_ddb = None


@dataclass
class Identity:
    method: str
    name: str
    email: str | None = None
    permissions: list = field(default_factory=lambda: ["read"])

    @property
    def can_write(self) -> bool:
        return "write" in self.permissions


def _table():
    global _ddb
    if _ddb is None:
        import boto3
        _ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    return _ddb.Table(API_KEYS_TABLE)


def _extract_key() -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer sk_"):
        return auth[7:]
    h = request.headers.get("X-API-Key", "")
    if h.startswith("sk_"):
        return h
    q = request.args.get("key", "")
    if q.startswith("sk_"):
        return q
    return None


def validate(key: str) -> Identity | None:
    if not key or not key.startswith("sk_"):
        return None
    try:
        resp = _table().get_item(Key={"key_id": key})
        item = resp.get("Item")
        if not item or not item.get("active", True):
            return None
        expires = item.get("expires_at")
        if expires and datetime.fromisoformat(expires) < datetime.now(timezone.utc):
            return None
        try:
            _table().update_item(
                Key={"key_id": key},
                UpdateExpression="SET last_used_at = :n",
                ExpressionAttributeValues={":n": datetime.now(timezone.utc).isoformat()},
            )
        except Exception:
            pass
        return Identity(
            method="api_key",
            name=item.get("name", key),
            email=item.get("email"),
            permissions=item.get("permissions", ["read"]),
        )
    except Exception:
        logger.exception("key validation failed")
        return None


def get_identity() -> Identity | None:
    k = _extract_key()
    return validate(k) if k else None


def require_auth(f):
    @wraps(f)
    def decorated(*a, **kw):
        ident = get_identity()
        if not ident:
            return jsonify({"error": "Authentication required"}), 401
        request.identity = ident
        return f(*a, **kw)
    return decorated
