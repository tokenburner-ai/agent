"""Agent admin API — accounts + context CRUD + IAM actions.

Generalized from kids-bedrock/manage.py. Instead of a hardcoded KIDS dict,
accounts live in the `tokenburner-agent-accounts` DynamoDB table.

Routes (all require require_auth — shared tokenburner api-keys.
Routes that change state additionally require the write permission
via require_write, since creating an account issues real IAM credentials):

  Accounts
  --------
  GET    /api/agent/accounts                 → list all accounts
  POST   /api/agent/accounts                 → create (body: {username, email?, tier?})
                                               * creates IAM user + access key + attaches tier policy
                                               * returns one-time access key secret
  GET    /api/agent/accounts/<username>      → details
  POST   /api/agent/accounts/<username>/disable → deactivate IAM access key
  POST   /api/agent/accounts/<username>/enable  → reactivate
  POST   /api/agent/accounts/<username>/tier    → body: {tier}
  DELETE /api/agent/accounts/<username>      → detach policy, delete key + user, DDB row

  Context
  -------
  GET    /api/agent/context?account=<name>   → {items: [...]}
  POST   /api/agent/context                  → body: {account_id, context_key, content, description?}
  DELETE /api/agent/context                  → body: {account_id, context_key}

Tiers map to IAM policies attached to the account's IAM user:
  basic : Haiku + Sonnet models
  pro   : basic + Opus
"""

import json
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from flask import Blueprint, jsonify, request

from auth import require_auth, require_write

admin_bp = Blueprint("admin_bp", __name__)

ACCOUNTS_TABLE = os.environ.get("ACCOUNTS_TABLE", "tokenburner-agent-accounts")
CONTEXT_TABLE = os.environ.get("CONTEXT_TABLE", "tokenburner-agent-context")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
TIER_BASIC_ARN = os.environ.get("TIER_BASIC_POLICY_ARN", "")
TIER_PRO_ARN = os.environ.get("TIER_PRO_POLICY_ARN", "")
IAM_PATH = "/tokenburner-agent/"

_ddb = None
_iam = None


def _ddb_res():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
    return _ddb


def _iam_client():
    global _iam
    if _iam is None:
        _iam = boto3.client("iam")
    return _iam


def _accounts():
    return _ddb_res().Table(ACCOUNTS_TABLE)


def _context():
    return _ddb_res().Table(CONTEXT_TABLE)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _tier_arn(tier: str) -> str:
    return {"basic": TIER_BASIC_ARN, "pro": TIER_PRO_ARN}.get(tier, TIER_BASIC_ARN)


# ─── Accounts ────────────────────────────────────────────

@admin_bp.route("/api/agent/accounts")
@require_auth
def list_accounts():
    resp = _accounts().scan()
    items = sorted(resp.get("Items", []), key=lambda i: i.get("username", ""))
    return jsonify({"accounts": items})


@admin_bp.route("/api/agent/accounts", methods=["POST"])
@require_write
def create_account():
    body = request.get_json() or {}
    username = (body.get("username") or "").strip().lower()
    email = (body.get("email") or "").strip() or None
    tier = (body.get("tier") or "basic").strip().lower()
    if not username or not username.replace("-", "").isalnum():
        return jsonify({"error": "username must be alphanumeric or hyphenated"}), 400
    if tier not in ("basic", "pro"):
        return jsonify({"error": "tier must be basic or pro"}), 400

    # Refuse if already exists
    if _accounts().get_item(Key={"username": username}).get("Item"):
        return jsonify({"error": "account already exists"}), 409

    iam = _iam_client()
    iam_user = f"tokenburner-agent-{username}"
    try:
        iam.create_user(UserName=iam_user, Path=IAM_PATH)
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            return jsonify({"error": f"create_user failed: {e}"}), 500

    policy_arn = _tier_arn(tier)
    if policy_arn:
        try:
            iam.attach_user_policy(UserName=iam_user, PolicyArn=policy_arn)
        except ClientError as e:
            return jsonify({"error": f"attach_user_policy failed: {e}"}), 500

    try:
        key = iam.create_access_key(UserName=iam_user)["AccessKey"]
    except ClientError as e:
        return jsonify({"error": f"create_access_key failed: {e}"}), 500

    item = {
        "username": username,
        "iam_user": iam_user,
        "access_key_id": key["AccessKeyId"],
        "status": "active",
        "tier": tier,
        "allowed_models": ["haiku", "sonnet"] if tier == "basic" else ["haiku", "sonnet", "opus"],
        "email": email or "",
        "created_at": _now(),
        "created_by": request.identity.name,
    }
    _accounts().put_item(Item=item)

    response_body = dict(item)
    response_body["secret_access_key"] = key["SecretAccessKey"]
    response_body["_note"] = "Save the secret_access_key now — it is never shown again."
    return jsonify(response_body), 201


@admin_bp.route("/api/agent/accounts/<username>")
@require_auth
def get_account(username):
    item = _accounts().get_item(Key={"username": username}).get("Item")
    if not item:
        return jsonify({"error": "not found"}), 404
    return jsonify(item)


@admin_bp.route("/api/agent/accounts/<username>/disable", methods=["POST"])
@require_write
def disable_account(username):
    item = _accounts().get_item(Key={"username": username}).get("Item")
    if not item:
        return jsonify({"error": "not found"}), 404
    _iam_client().update_access_key(
        UserName=item["iam_user"], AccessKeyId=item["access_key_id"], Status="Inactive",
    )
    _accounts().update_item(
        Key={"username": username},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "disabled"},
    )
    return jsonify({"username": username, "status": "disabled"})


@admin_bp.route("/api/agent/accounts/<username>/enable", methods=["POST"])
@require_write
def enable_account(username):
    item = _accounts().get_item(Key={"username": username}).get("Item")
    if not item:
        return jsonify({"error": "not found"}), 404
    _iam_client().update_access_key(
        UserName=item["iam_user"], AccessKeyId=item["access_key_id"], Status="Active",
    )
    _accounts().update_item(
        Key={"username": username},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "active"},
    )
    return jsonify({"username": username, "status": "active"})


@admin_bp.route("/api/agent/accounts/<username>/tier", methods=["POST"])
@require_write
def set_tier(username):
    body = request.get_json() or {}
    tier = (body.get("tier") or "").strip().lower()
    if tier not in ("basic", "pro"):
        return jsonify({"error": "tier must be basic or pro"}), 400

    item = _accounts().get_item(Key={"username": username}).get("Item")
    if not item:
        return jsonify({"error": "not found"}), 404

    iam = _iam_client()
    # Detach current policies under our path and attach the new one.
    policies = iam.list_attached_user_policies(UserName=item["iam_user"]).get("AttachedPolicies", [])
    for p in policies:
        if p["PolicyArn"] in (TIER_BASIC_ARN, TIER_PRO_ARN):
            iam.detach_user_policy(UserName=item["iam_user"], PolicyArn=p["PolicyArn"])
    new_arn = _tier_arn(tier)
    if new_arn:
        iam.attach_user_policy(UserName=item["iam_user"], PolicyArn=new_arn)

    _accounts().update_item(
        Key={"username": username},
        UpdateExpression="SET tier = :t, allowed_models = :m",
        ExpressionAttributeValues={
            ":t": tier,
            ":m": ["haiku", "sonnet"] if tier == "basic" else ["haiku", "sonnet", "opus"],
        },
    )
    return jsonify({"username": username, "tier": tier})


@admin_bp.route("/api/agent/accounts/<username>", methods=["DELETE"])
@require_write
def delete_account(username):
    item = _accounts().get_item(Key={"username": username}).get("Item")
    if not item:
        return jsonify({"error": "not found"}), 404
    iam = _iam_client()
    try:
        for p in iam.list_attached_user_policies(UserName=item["iam_user"]).get("AttachedPolicies", []):
            iam.detach_user_policy(UserName=item["iam_user"], PolicyArn=p["PolicyArn"])
        try:
            iam.delete_access_key(UserName=item["iam_user"], AccessKeyId=item["access_key_id"])
        except ClientError:
            pass
        iam.delete_user(UserName=item["iam_user"])
    except ClientError as e:
        return jsonify({"error": f"iam cleanup failed: {e}"}), 500
    _accounts().delete_item(Key={"username": username})
    return jsonify({"username": username, "deleted": True})


# ─── Context ─────────────────────────────────────────────

@admin_bp.route("/api/agent/context")
@require_auth
def list_context():
    account = (request.args.get("account") or "").strip().lower()
    if not account:
        return jsonify({"error": "account query param required (use _shared for shared entries)"}), 400
    resp = _context().query(KeyConditionExpression=Key("account_id").eq(account))
    items = [i for i in resp.get("Items", []) if i.get("active", True)]
    items.sort(key=lambda i: i.get("context_key", ""))
    return jsonify({"items": items})


@admin_bp.route("/api/agent/context", methods=["POST"])
@require_write
def put_context():
    body = request.get_json() or {}
    account_id = (body.get("account_id") or "").strip().lower()
    context_key = (body.get("context_key") or "").strip()
    content = body.get("content") or ""
    description = body.get("description") or ""
    if not account_id or not context_key:
        return jsonify({"error": "account_id and context_key required"}), 400
    item = {
        "account_id": account_id,
        "context_key": context_key,
        "content": content,
        "description": description,
        "active": True,
        "updated_at": _now(),
        "updated_by": request.identity.name,
    }
    _context().put_item(Item=item)
    return jsonify(item), 201


@admin_bp.route("/api/agent/context", methods=["DELETE"])
@require_write
def delete_context():
    body = request.get_json() or {}
    account_id = (body.get("account_id") or "").strip().lower()
    context_key = (body.get("context_key") or "").strip()
    if not account_id or not context_key:
        return jsonify({"error": "account_id and context_key required"}), 400
    _context().delete_item(Key={"account_id": account_id, "context_key": context_key})
    return jsonify({"account_id": account_id, "context_key": context_key, "deleted": True})
