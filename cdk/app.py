#!/usr/bin/env python3
"""Agent stack — CDK entry point."""

import os
import aws_cdk as cdk
from stack import AgentStack

app = cdk.App()
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-west-2"),
)
AgentStack(app, "tokenburner-agent", env=env)
app.synth()
