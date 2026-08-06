"""Context API — plain Lambda handler for reading context entries.

Exposed via a Lambda function URL with AWS_IAM auth. Each provisioned account
signs requests with its own IAM access key. The account is derived from the
signed caller, so the response is always scoped to that account's own rows plus
`_shared`, whatever the request asks for.

GET /
GET /?key=<context_key>
GET /?account=<own name>          (accepted only when it matches the caller)

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


IAM_USER_PREFIX = "tokenburner-agent-"


def _caller_account(event) -> str:
    """Return the account name of the signed caller, or "" if it is not an account.

    The Function URL uses AWS_IAM auth, so the authorizer gives us the caller's
    IAM user ARN. Accounts are provisioned as IAM users named
    tokenburner-agent-<account>, so the account name is derivable from the
    principal and never needs to be taken from the request.
    """
    iam_ctx = ((event.get("requestContext") or {}).get("authorizer") or {}).get("iam") or {}
    arn = iam_ctx.get("userArn") or ""
    user = arn.rsplit("/", 1)[-1] if arn else ""
    if not user.startswith(IAM_USER_PREFIX):
        return ""
    return user[len(IAM_USER_PREFIX):].strip().lower()


def handler(event, _ctx):
    params = event.get("queryStringParameters") or {}

    # Scope the response to the signed caller. Trusting ?account= let any
    # provisioned account read any other account's rows by naming it.
    account = _caller_account(event)
    if not account:
        return _resp(403, {"error": "caller is not a provisioned account"})

    requested = (params.get("account") or "").strip().lower()
    if requested and requested != account:
        return _resp(403, {"error": "cannot read another account's context"})

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
