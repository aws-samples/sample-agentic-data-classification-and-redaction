"""
Seed / update the externalized MNPI classification prompt in Amazon Bedrock
Prompt Management, then publish its ARN to SSM Parameter Store so the
Classification Lambda can look it up at runtime.

Why this exists:
  MNPI (Material Non-Public Information) is a moving target -- what counts as
  MNPI evolves over time. Keeping the classification prompt in Bedrock Prompt
  Management lets authorized users edit the rules in the Bedrock console (which
  edits the DRAFT version) and have changes take effect immediately, with no
  Lambda code change or redeployment.

What it does (idempotent):
  1. Looks up a prompt named "<project>-<env>-classification".
  2. Creates it if missing, otherwise updates its working DRAFT.
  3. Writes the prompt ARN to SSM at /<project>/<env>/classification-prompt-arn.

The Lambda invokes the DRAFT directly (bare prompt ARN, no version suffix), so
console edits go live automatically. The prompt owns the model ID and inference
configuration, so those can also be changed without redeploying the Lambda.

Usage:
    python3 scripts/seed-classification-prompt.py [environment] [model_id]
"""

import sys

import boto3

REGION = "us-east-1"
PROJECT = "data-classification"
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"

# The variable substituted at invocation time. In Prompt Management, variables
# use the {{name}} double-brace syntax; the literal single braces in the JSON
# output schema below are passed through untouched.
CLASSIFICATION_PROMPT_TEXT = """You are a data classification system for a financial services firm. Analyze the following content and return a JSON classification.

You MUST assess three dimensions:

1. **MNPI Assessment** — Is this Material Non-Public Information?
   MNPI means the content itself contains material, non-public financial information that could move a stock price or violate securities regulations if traded upon.

   IS MNPI:
   - Specific earnings figures not yet publicly announced
   - Undisclosed M&A activity, deal terms, or acquisition targets
   - Client portfolio positions or upcoming allocation changes
   - Revenue guidance, buyback plans, or financial projections not yet filed/disclosed
   - Expert insights revealing undisclosed company financials or strategic plans

   IS NOT MNPI:
   - Administrative references to compliance processes (e.g., "employee needs wall-crossing", "complete compliance training")
   - General HR records, onboarding documents, or personnel files — even if they mention company names in an administrative context
   - Internal scheduling, team standups, or process discussions that don't reveal financial details
   - Publicly available news articles, press releases, or published research

   Key distinction: A document that MENTIONS a company name is not MNPI. A document that reveals UNDISCLOSED FINANCIAL DETAILS about that company is MNPI.

   - mnpi: boolean (true ONLY if the content itself contains material non-public financial information)
   - mnpi_confidence: float 0.0-1.0 (how confident you are)
   - mnpi_entities: list of strings (companies/entities the MNPI relates to — only include if actual MNPI about them is present)
   - mnpi_reasoning: string (brief explanation)

2. **PII Detection** — What personally identifiable information is present?
   Types to detect: email_address, phone_number, ssn, name, address, financial_account,
   date_of_birth, ip_address, credit_card
   - pii_detected: boolean (true if any PII found)
   - pii_types: list of strings (types of PII found)
   - pii_entities: list of objects with {type, value, location} for each PII item found

3. **Security Level** — What is the appropriate security classification?
   - Public: No restrictions, publicly available information
   - Internal: Firm employees only, general business information
   - Confidential: Need-to-know basis, sensitive business or personal details (e.g., HR records, salary info)
   - Restricted: Named individuals only, highly sensitive (contains actual MNPI, client-specific deal terms)
   - security_level: string (one of Public, Internal, Confidential, Restricted)
   - security_reasoning: string (brief explanation)

Content to classify:
---
{{content}}
---

Return ONLY valid JSON with this exact structure (no markdown, no explanation outside JSON):
{
  "mnpi": false,
  "mnpi_confidence": 0.0,
  "mnpi_entities": [],
  "mnpi_reasoning": "",
  "pii_detected": false,
  "pii_types": [],
  "pii_entities": [],
  "security_level": "Public",
  "security_reasoning": ""
}"""

VARIANT_NAME = "default"
MAX_TOKENS = 2048
TEMPERATURE = 0.0


def prompt_name(environment):
    return f"{PROJECT}-{environment}-classification"


def ssm_param_name(environment):
    return f"/{PROJECT}/{environment}/classification-prompt-arn"


def build_variant(model_id):
    """A single TEXT variant that owns the model + inference config."""
    return {
        "name": VARIANT_NAME,
        "templateType": "TEXT",
        "modelId": model_id,
        "inferenceConfiguration": {
            "text": {
                "maxTokens": MAX_TOKENS,
                "temperature": TEMPERATURE,
            }
        },
        "templateConfiguration": {
            "text": {
                "text": CLASSIFICATION_PROMPT_TEXT,
                "inputVariables": [{"name": "content"}],
            }
        },
    }


def find_existing_prompt(bedrock_agent, name):
    """Return the prompt id if a prompt with this name already exists, else None."""
    paginator = bedrock_agent.get_paginator("list_prompts")
    for page in paginator.paginate():
        for summary in page.get("promptSummaries", []):
            if summary.get("name") == name:
                return summary["id"]
    return None


def seed_prompt(environment="demo", model_id=DEFAULT_MODEL_ID):
    bedrock_agent = boto3.client("bedrock-agent", region_name=REGION)
    ssm = boto3.client("ssm", region_name=REGION)

    name = prompt_name(environment)
    variant = build_variant(model_id)

    existing_id = find_existing_prompt(bedrock_agent, name)

    if existing_id:
        print(f"Updating existing prompt DRAFT: {name} ({existing_id})")
        response = bedrock_agent.update_prompt(
            promptIdentifier=existing_id,
            name=name,
            description="MNPI/PII/security classification prompt (managed, editable in console).",
            defaultVariant=VARIANT_NAME,
            variants=[variant],
        )
    else:
        print(f"Creating prompt: {name}")
        response = bedrock_agent.create_prompt(
            name=name,
            description="MNPI/PII/security classification prompt (managed, editable in console).",
            defaultVariant=VARIANT_NAME,
            variants=[variant],
        )

    # The DRAFT ARN (no version suffix) — invoking it always resolves to the
    # latest working draft, so console edits go live automatically.
    prompt_arn = response["arn"]
    print(f"  Prompt ARN (DRAFT): {prompt_arn}")
    print(f"  Model ID:           {model_id}")

    param_name = ssm_param_name(environment)
    ssm.put_parameter(
        Name=param_name,
        Value=prompt_arn,
        Type="String",
        Overwrite=True,
        Description="ARN of the active MNPI classification prompt (Bedrock Prompt Management).",
    )
    print(f"  Published ARN to SSM: {param_name}")

    print("\nDone. The Classification Lambda will pick up this prompt on its next invocation.")
    print("To evolve the MNPI rules later, edit the prompt DRAFT in the Bedrock console")
    print("(Prompt management) — changes take effect with no redeployment.")

    return prompt_arn


if __name__ == "__main__":
    env = sys.argv[1] if len(sys.argv) > 1 else "demo"
    model = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL_ID
    seed_prompt(env, model)
