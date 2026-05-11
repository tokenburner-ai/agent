# Token Agent

A generalized personal-assistant agent. Ships two Lambdas:

- **admin-api** — the backend for the Administration dashboard card. Lets an
  admin create accounts, toggle enable/disable, rotate IAM keys, switch model
  tiers, and edit per-account or shared context entries. Uses the shared
  `tokenburner-api-keys` DynamoDB table for its own auth.
- **context-api** — a Function URL consumed by the agent's MCP server on a
  user's machine. IAM-signed (the account's own AWS access key signs the
  request) so no secret key needs to be shipped.

Two DynamoDB tables, both created by this stack:

- `tokenburner-agent-accounts` — `{username, iam_user, access_key_id, status,
  allowed_models, tier, created_at, created_by, email}`. Replaces the old
  hardcoded dict that lived in kids-bedrock/manage.py.
- `tokenburner-agent-context` — `{account_id, context_key, content,
  description, active, updated_at}`. `account_id = "_shared"` for context
  that applies to every account.

Part of the tokenburner feature suite — registers **two** cards on the
dashboard (Agent + Administration) via the shared feature-registry.

## Quick start

```bash
cd ../stack
python3 tokenburner.py install --features agent
```

Outputs:
- `AgentAdminUrl` — the Administration card target (the dashboard iframes /
  links into this).
- `AgentContextFunctionUrl` — IAM-signed URL for the MCP server on a user's
  machine.

## Desktop app

The Electron app in `./app/` (stub until Day 4+) is packaged via GitHub
Actions and published to a public S3 bucket for download. The installer
reads the AWS credentials provisioned by `admin-api` on account creation.

## File map

```
agent/
├── tokenburner.md            # This file
├── app/
│   ├── main.py               # Admin Flask app
│   ├── auth.py               # Shared require_auth
│   ├── admin_api.py          # Accounts + context CRUD, IAM actions
│   └── context_api_handler.py# Plain Lambda handler (no Flask) for MCP context reads
├── static/
│   └── admin.html            # Administration mini-SPA
├── cdk/
│   ├── app.py
│   ├── stack.py              # Two Lambdas, two DDB tables, two registry rows
│   ├── cdk.json
│   └── requirements.txt
├── mcp-server/               # (stub) MCP server that calls context-api
├── setup/                    # (stub) onboard-a-machine script
├── lambda_handler.py
└── requirements.txt
```
