"""
Seed the EntitlementPolicies DynamoDB table with demo user entitlements.
These represent different user personas with varying access levels.
"""

import boto3
import sys

REGION = "us-east-1"


def get_table_name(environment="demo"):
    return f"data-classification-{environment}-entitlement-policies"


def seed_entitlements(environment="demo"):
    """Seed demo user entitlements."""
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table_name = get_table_name(environment)
    table = dynamodb.Table(table_name)

    users = [
        {
            "principal_id": "alice-pm",
            "display_name": "Alice Chen (Portfolio Manager)",
            "role": "Portfolio Manager",
            "max_security_level": "Restricted",
            "mnpi_cleared_entities": ["ACME Corp", "TechStart Inc.", "GlobalTech Industries", "NovaTech Systems"],
            "pii_access": False,
            "description": "Senior PM, wall-crossed for ACME, TechStart, GlobalTech, and NovaTech. Full MNPI access. No PII access.",
        },
        {
            "principal_id": "bob-analyst",
            "display_name": "Bob Martinez (Research Analyst)",
            "role": "Research Analyst",
            "max_security_level": "Restricted",
            "mnpi_cleared_entities": ["ACME Corp", "TechStart Inc."],
            "pii_access": False,
            "description": "Analyst with Restricted access. Wall-crossed for ACME/TechStart only. Cannot see GlobalTech/NovaTech MNPI. No PII access.",
        },
        {
            "principal_id": "carol-compliance",
            "display_name": "Carol Davis (Compliance Officer)",
            "role": "Compliance Officer",
            "max_security_level": "Restricted",
            "mnpi_cleared_entities": ["ACME Corp", "TechStart Inc.", "GlobalTech Industries", "NovaTech Systems"],
            "pii_access": True,
            "description": "Full access including PII for compliance investigations. Sees everything unredacted.",
        },
        {
            "principal_id": "dave-intern",
            "display_name": "Dave Wilson (Summer Intern)",
            "role": "Intern",
            "max_security_level": "Internal",
            "mnpi_cleared_entities": [],
            "pii_access": False,
            "description": "Intern with no MNPI access and only Internal security clearance.",
        },
        {
            "principal_id": "eve-hr",
            "display_name": "Eve Johnson (HR Manager)",
            "role": "HR Manager",
            "max_security_level": "Restricted",
            "mnpi_cleared_entities": [],
            "pii_access": True,
            "description": "HR Manager with Restricted access and PII access. No MNPI wall-crossing at all.",
        },
    ]

    print(f"Seeding {len(users)} user entitlements to {table_name}...")

    for user in users:
        table.put_item(Item=user)
        print(f"  Created: {user['principal_id']} ({user['role']})")

    print("\nDone! User entitlements seeded successfully.")
    print("\nDemo users and their access:")
    print("-" * 70)
    for user in users:
        print(f"  {user['display_name']}")
        print(f"    Security Level: {user['max_security_level']}")
        print(f"    MNPI Cleared:   {user['mnpi_cleared_entities'] or 'None'}")
        print(f"    PII Access:     {user['pii_access']}")
        print()


if __name__ == "__main__":
    env = sys.argv[1] if len(sys.argv) > 1 else "demo"
    seed_entitlements(env)
