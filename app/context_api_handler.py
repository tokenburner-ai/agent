"""Context API — plain Lambda handler for reading context entries.

Exposed via a Lambda function URL with AWS_IAM auth. Each provisioned account
signs requests with its own IAM access key, so the response is scoped to what
that account is allowed to read: its own context rows plus `_shared`.

GET /?account=<name>
GET /?account=<name>&key=<context_key>

Response: {"context": [{account_id, context_key, content, description, updated_at}, ...]}
"""

import json
import os

import boto3
from boto3.dynamodb.conditions import Key

CONTEXT_TABLE = os.environ.get("CONTEXT_TABLE", "tokenburner-agent-context")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

_table = None


def _t():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(CONTEXT_TABLE)
    return _table


def _clean(item):
    return {
        "account_id": item.get("account_id", ""),
        "context_key": item.get("context_key", ""),
        "content": item.get("content", ""),
        "description": item.get("description", ""),
        "updated_at": item.get("updated_at", ""),
    }


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def handler(event, _ctx):
    params = event.get("queryStringParameters") or {}
    account = (params.get("account") or "").strip().lower()
    if not account:
        return _resp(400, {"error": "missing ?account= parameter"})

    key = params.get("key")
    items = []
    ids = [account, "_shared"]

    if key:
        for aid in ids:
            resp = _t().get_item(Key={"account_id": aid, "context_key": key})
            item = resp.get("Item")
            if item and item.get("active", True):
                items.append(_clean(item))
        return _resp(200, {"context": items})

    for aid in ids:
        resp = _t().query(KeyConditionExpression=Key("account_id").eq(aid))
        for item in resp.get("Items", []):
            if item.get("active", True):
                items.append(_clean(item))
    return _resp(200, {"context": items})
