"""Token Agent Stack — admin-api + context-api, accounts + context DDB tables.

Pre-requisite: tokenburner-base stack must be deployed.
Imports: tokenburner-api-keys-table-name/arn, tokenburner-feature-registry-table-name/arn

Registers TWO rows in the feature registry:
  name=agent           → end-user card, links to the admin SPA / Electron downloads
  name=administration  → admin card, same URL, but separate so the dashboard
                          can display both.
"""

import json
import os
import aws_cdk as cdk
from aws_cdk import (
    aws_lambda as _lambda,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    custom_resources as cr,
)
from constructs import Construct

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")


class AgentStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        api_keys_table_name = cdk.Fn.import_value("tokenburner-api-keys-table-name")
        api_keys_table_arn  = cdk.Fn.import_value("tokenburner-api-keys-table-arn")
        feature_registry_table_name = cdk.Fn.import_value("tokenburner-feature-registry-table-name")
        feature_registry_table_arn  = cdk.Fn.import_value("tokenburner-feature-registry-table-arn")

        cdk.Tags.of(self).add("ManagedBy", "tokenburner")
        cdk.Tags.of(self).add("tokenburner:feature", "agent")

        # ── DDB tables ───────────────────────────────────────────────────────
        accounts_table = dynamodb.Table(
            self, "AccountsTable",
            table_name="tokenburner-agent-accounts",
            partition_key=dynamodb.Attribute(name="username", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        context_table = dynamodb.Table(
            self, "ContextTable",
            table_name="tokenburner-agent-context",
            partition_key=dynamodb.Attribute(name="account_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="context_key", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # ── Tier policies (attached to per-account IAM users) ────────────────
        # IAM policy names are account-global, so suffix with region to allow
        # agent to be deployed in multiple regions of the same account.
        basic_policy = iam.ManagedPolicy(
            self, "TierBasic",
            managed_policy_name=f"tokenburner-agent-tier-basic-{self.region}",
            description="Bedrock Haiku + Sonnet access for tokenburner-agent accounts",
            statements=[
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                             "bedrock:Converse", "bedrock:ConverseStream"],
                    resources=[
                        "arn:aws:bedrock:*::foundation-model/anthropic.claude-*haiku*",
                        "arn:aws:bedrock:*::foundation-model/anthropic.claude-*sonnet*",
                        "arn:aws:bedrock:*:*:inference-profile/us.anthropic.claude-*haiku*",
                        "arn:aws:bedrock:*:*:inference-profile/us.anthropic.claude-*sonnet*",
                    ],
                ),
            ],
        )
        pro_policy = iam.ManagedPolicy(
            self, "TierPro",
            managed_policy_name=f"tokenburner-agent-tier-pro-{self.region}",
            description="Bedrock Haiku + Sonnet + Opus access for tokenburner-agent pro tier",
            statements=[
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                             "bedrock:Converse", "bedrock:ConverseStream"],
                    resources=[
                        "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                        "arn:aws:bedrock:*:*:inference-profile/us.anthropic.claude-*",
                    ],
                ),
            ],
        )

        # ── admin-api Lambda (behind base-stack api-key) ────────────────────
        admin_fn = _lambda.Function(
            self, "AdminFn",
            function_name="tokenburner-agent-admin",
            runtime=_lambda.Runtime.PYTHON_3_12,
            architecture=_lambda.Architecture.ARM_64,
            handler="lambda_handler.handler",
            memory_size=512,
            timeout=cdk.Duration.seconds(60),
            code=_lambda.Code.from_asset(
                path=PROJECT_ROOT,
                bundling=cdk.BundlingOptions(
                    image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                    platform="linux/arm64",
                    command=[
                        "bash", "-c",
                        "pip install -r requirements.txt -t /asset-output --quiet && "
                        "cp -r app /asset-output/ && "
                        "cp lambda_handler.py /asset-output/ && "
                        "cp -r static /asset-output/",
                    ],
                ),
            ),
            environment={
                "ACCOUNTS_TABLE": accounts_table.table_name,
                "CONTEXT_TABLE": context_table.table_name,
                "API_KEYS_TABLE": api_keys_table_name,
                "TIER_BASIC_POLICY_ARN": basic_policy.managed_policy_arn,
                "TIER_PRO_POLICY_ARN": pro_policy.managed_policy_arn,
            },
        )

        accounts_table.grant_read_write_data(admin_fn)
        context_table.grant_read_write_data(admin_fn)
        admin_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:UpdateItem"],
            resources=[api_keys_table_arn],
        ))
        admin_fn.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "iam:CreateUser", "iam:DeleteUser", "iam:GetUser",
                "iam:CreateAccessKey", "iam:DeleteAccessKey", "iam:UpdateAccessKey",
                "iam:AttachUserPolicy", "iam:DetachUserPolicy", "iam:ListAttachedUserPolicies",
            ],
            resources=[f"arn:aws:iam::{self.account}:user/tokenburner-agent/*"],
        ))

        admin_url = admin_fn.add_function_url(auth_type=_lambda.FunctionUrlAuthType.NONE)

        admin_distribution = cloudfront.Distribution(
            self, "AdminCdn",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.FunctionUrlOrigin(admin_url),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
            ),
        )

        # ── context-api Lambda (IAM-signed Function URL) ─────────────────────
        context_fn = _lambda.Function(
            self, "ContextFn",
            function_name="tokenburner-agent-context",
            runtime=_lambda.Runtime.PYTHON_3_12,
            architecture=_lambda.Architecture.ARM_64,
            handler="context_api_handler.handler",
            memory_size=256,
            timeout=cdk.Duration.seconds(10),
            code=_lambda.Code.from_asset(
                path=os.path.join(PROJECT_ROOT, "app"),
                exclude=["__pycache__"],
            ),
            environment={
                "CONTEXT_TABLE": context_table.table_name,
            },
        )
        context_table.grant_read_data(context_fn)
        context_fn_url = context_fn.add_function_url(auth_type=_lambda.FunctionUrlAuthType.AWS_IAM)

        # Grant every provisioned agent account the right to invoke the URL.
        # Scoped to IAM users under our path so random account users can't invoke it.
        context_fn.add_permission(
            "AllowAgentAccounts",
            principal=iam.AccountRootPrincipal(),
            action="lambda:InvokeFunctionUrl",
            function_url_auth_type=_lambda.FunctionUrlAuthType.AWS_IAM,
        )
        # Also attach invoke permission via a managed policy attached to tier users.
        # Function URLs created since October 2025 require both actions. With
        # only InvokeFunctionUrl granted, every provisioned account gets 403
        # from the context URL and can never read its own context.
        #
        # InvokeFunction is constrained to invocation through the Function URL.
        # Granted unconditionally it also permits `lambda:Invoke` directly, and
        # a direct caller supplies the whole event, including the authorizer
        # block the handler reads the caller identity from. An account could
        # then name any other account and read its rows.
        invoke_policy_statements = [
            iam.PolicyStatement(
                actions=["lambda:InvokeFunctionUrl"],
                resources=[context_fn.function_arn],
                conditions={
                    "StringEquals": {"lambda:FunctionUrlAuthType": "AWS_IAM"},
                },
            ),
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[context_fn.function_arn],
                conditions={
                    "Bool": {"lambda:InvokedViaFunctionUrl": "true"},
                },
            ),
        ]
        for policy in (basic_policy, pro_policy):
            for stmt in invoke_policy_statements:
                policy.add_statements(stmt)

        # ── Self-register (two rows: agent + administration) ────────────────
        admin_cdn_url = f"https://{admin_distribution.distribution_domain_name}"

        def register(construct_id: str, name: str, title: str, description: str):
            reg = cr.AwsCustomResource(
                self, construct_id,
                on_create=cr.AwsSdkCall(
                    service="DynamoDB", action="putItem",
                    physical_resource_id=cr.PhysicalResourceId.of(f"{name}-registry"),
                    parameters={
                        "TableName": feature_registry_table_name,
                        "Item": {
                            "name":        {"S": name},
                            "title":       {"S": title},
                            "description": {"S": description},
                            "url":         {"S": admin_cdn_url},
                            "docs_url":    {"S": admin_cdn_url},
                            "health_url":  {"S": f"{admin_cdn_url}/health"},
                            "stack_name":  {"S": cdk.Aws.STACK_NAME},
                        },
                    },
                ),
                on_update=cr.AwsSdkCall(
                    service="DynamoDB", action="putItem",
                    physical_resource_id=cr.PhysicalResourceId.of(f"{name}-registry"),
                    parameters={
                        "TableName": feature_registry_table_name,
                        "Item": {
                            "name":        {"S": name},
                            "title":       {"S": title},
                            "description": {"S": description},
                            "url":         {"S": admin_cdn_url},
                            "docs_url":    {"S": admin_cdn_url},
                            "health_url":  {"S": f"{admin_cdn_url}/health"},
                            "stack_name":  {"S": cdk.Aws.STACK_NAME},
                        },
                    },
                ),
                on_delete=cr.AwsSdkCall(
                    service="DynamoDB", action="deleteItem",
                    parameters={
                        "TableName": feature_registry_table_name,
                        "Key": {"name": {"S": name}},
                    },
                ),
                policy=cr.AwsCustomResourcePolicy.from_statements([
                    iam.PolicyStatement(
                        actions=["dynamodb:PutItem", "dynamodb:DeleteItem"],
                        resources=[feature_registry_table_arn],
                    ),
                ]),
            )
            reg.node.add_dependency(admin_distribution)
            return reg

        register("RegisterAgent", "agent", "Token Agent",
                 "Desktop agent app plus admin console for managing accounts and context.")
        register("RegisterAdmin", "administration", "Administration",
                 "Manage tokenburner accounts, IAM keys, model tiers, and shared context.")

        cdk.CfnOutput(self, "AgentAdminUrl", value=admin_cdn_url)
        cdk.CfnOutput(self, "AgentContextFunctionUrl", value=context_fn_url.url,
                       description="IAM-signed URL for MCP server context reads")
        cdk.CfnOutput(self, "TierBasicPolicy", value=basic_policy.managed_policy_arn)
        cdk.CfnOutput(self, "TierProPolicy", value=pro_policy.managed_policy_arn)
