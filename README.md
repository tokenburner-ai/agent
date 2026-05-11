# Token Agent

Account + context management for the
[tokenburner](https://github.com/tokenburner-ai/stack) suite. Registers two
cards on the dashboard:

- **Agent** — end-user surface (desktop app download, onboarding).
- **Administration** — admin console: create agent accounts (provisions IAM
  users with scoped Bedrock access), edit per-account or shared context,
  toggle tiers.

Ships a second Lambda behind an IAM-signed Function URL for MCP-style
context reads from a user's machine.

## Install

```bash
git clone https://github.com/tokenburner-ai/stack.git
cd stack
python3 tokenburner.py install --features agent
```

## Standalone

The base stack must be deployed first. Then:

```bash
cd cdk
AWS_PROFILE=<profile> \
  CDK_DEFAULT_ACCOUNT=$(AWS_PROFILE=<profile> aws sts get-caller-identity --query Account --output text) \
  CDK_DEFAULT_REGION=us-west-2 \
  npx cdk deploy tokenburner-agent --require-approval never
```

See [`tokenburner.md`](./tokenburner.md) for the full feature spec.
