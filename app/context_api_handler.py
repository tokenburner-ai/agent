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
import re

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

# Only an IAM user provisioned by the admin API counts. Matching on the last
# ARN segment alone is not enough: an assumed-role ARN ends with a session name
# the caller chooses, so a role session called tokenburner-agent-<name> would
# otherwise be read as that account.
_CALLER_ARN = re.compile(
    r"^arn:aws[a-z0-9-]*:iam::\d{12}:user/tokenburner-agent/"
    + re.escape(IAM_USER_PREFIX)
    + r"(?P<account>[A-Za-z0-9_.@-]+)$"
)


def _caller_account(event) -> str:
    """Return the account name of the signed caller, or "" if it is not one.

    The Function URL uses AWS_IAM auth, so the authorizer reports the caller's
    IAM user ARN. Accounts are provisioned as IAM users named
    tokenburner-agent-<account> under the /tokenburner-agent/ path, so the whole
    ARN shape is required rather than just its last segment.
    """
    iam_ctx = ((event.get("requestContext") or {}).get("authorizer") or {}).get("iam") or {}
    match = _CALLER_ARN.match(iam_ctx.get("userArn") or "")
    return match.group("account").lower() if match else ""


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
