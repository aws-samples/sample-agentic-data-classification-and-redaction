"""
Generate sample PDF documents for the classification demo.
Creates realistic-looking PDFs from sample content to demonstrate
the full pipeline: PDF upload → Textract extraction → Classification → Storage.
"""

import os
import sys

from fpdf import FPDF

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "sample-data")


class StyledPDF(FPDF):
    """PDF with consistent styling for demo documents."""

    def __init__(self):
        super().__init__()
        self.compress = False  # Uncompressed for Textract compatibility

    def header_block(self, title, subtitle="", classification=""):
        self.set_font("Helvetica", "B", 16)
        self.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        if subtitle:
            self.set_font("Helvetica", "I", 10)
            self.set_text_color(100, 100, 100)
            self.cell(0, 6, subtitle, new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)
        if classification:
            self.set_font("Helvetica", "B", 9)
            self.set_text_color(200, 0, 0)
            self.cell(0, 8, f"CLASSIFICATION: {classification}", new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)
        self.ln(5)

    def body_text(self, text):
        self.set_font("Helvetica", "", 10)
        self.multi_cell(0, 5, text)
        self.ln(3)

    def section_header(self, text):
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def metadata_line(self, label, value):
        self.set_font("Helvetica", "B", 9)
        self.cell(30, 5, f"{label}:")
        self.set_font("Helvetica", "", 9)
        self.cell(0, 5, value, new_x="LMARGIN", new_y="NEXT")


def create_mnpi_email_pdf():
    """ACME Corp earnings preview email — contains MNPI."""
    pdf = StyledPDF()
    pdf.add_page()
    pdf.header_block(
        "Internal Email Communication",
        "AnyCompany Financial - Confidential",
        "RESTRICTED - MNPI - DO NOT DISTRIBUTE",
    )

    pdf.metadata_line("From", "john.smith@fsicompany.com")
    pdf.metadata_line("To", "sarah.jones@fsicompany.com")
    pdf.metadata_line("Date", "July 15, 2026 09:30 AM EST")
    pdf.metadata_line("Subject", "ACME Corp - Q3 Earnings Preview (CONFIDENTIAL)")
    pdf.ln(8)

    pdf.body_text("Sarah,")
    pdf.body_text(
        "Following up on our call with ACME Corp's CFO yesterday. Key takeaways:"
    )
    pdf.ln(3)

    pdf.body_text(
        "1. Q3 revenue is tracking at $4.2B, which is approximately 15% above "
        "street consensus of $3.65B. This has not been disclosed publicly and "
        "won't be until the earnings call on August 14th."
    )
    pdf.body_text(
        "2. They're planning to announce a $2B share buyback program alongside "
        "earnings. The board approved it last week but it hasn't been filed with "
        "the SEC yet."
    )
    pdf.body_text(
        "3. The acquisition of TechStart Inc. is expected to close by end of August. "
        "Purchase price is $890M, which is a 40% premium to TechStart's last private "
        "valuation. This is still under NDA."
    )
    pdf.body_text(
        "4. Their new AI product line is exceeding internal targets - they expect to "
        "revise full-year guidance upward by 8-12% when they report."
    )
    pdf.ln(3)
    pdf.body_text(
        "Please keep this strictly confidential and do not share outside the "
        "wall-crossed team."
    )
    pdf.ln(5)
    pdf.body_text("Best,")
    pdf.body_text("John Smith")
    pdf.body_text("Senior Portfolio Manager")
    pdf.body_text("Direct: (203) 555-0147")
    pdf.body_text("john.smith@fsicompany.com")

    path = os.path.join(OUTPUT_DIR, "email-mnpi-acme.pdf")
    pdf.output(path)
    print(f"  Created: {path}")


def create_expert_transcript_pdf():
    """Expert network call transcript — contains MNPI + PII."""
    pdf = StyledPDF()
    pdf.add_page()
    pdf.header_block(
        "Expert Network Call Transcript",
        "Confidential - For Authorized Personnel Only",
        "RESTRICTED - PENDING COMPLIANCE REVIEW",
    )

    pdf.metadata_line("Date", "July 20, 2026")
    pdf.metadata_line("Expert", "Dr. Michael Chen, Former VP of Engineering at GlobalTech Industries")
    pdf.metadata_line("Topic", "AI Infrastructure Market Dynamics")
    pdf.metadata_line("Duration", "45 minutes")
    pdf.metadata_line("Compliance Note", "This transcript has NOT been reviewed for MNPI content")
    pdf.ln(8)

    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)

    conversations = [
        ("ANALYST", "Thank you for joining us today, Dr. Chen. Can you walk us through what you're seeing in the enterprise AI infrastructure market?"),
        ("EXPERT", "Absolutely. Based on my conversations with former colleagues still at GlobalTech, I can share that the company is about to make a significant pivot. They've been quietly building a new GPU cluster management platform that they plan to announce at their developer conference in September."),
        ("ANALYST", "That's interesting. Can you give us a sense of the investment scale?"),
        ("EXPERT", "From what I understand, GlobalTech has committed $3.5 billion in capex for this initiative over the next 18 months. This hasn't been disclosed in any public filing yet. Their current guidance only reflects about $1.2 billion in AI-related spending."),
        ("ANALYST", "How does this compare to what you see from competitors?"),
        ("EXPERT", "Well, I know that NovaTech Systems - where my former colleague James Rodriguez (james.rodriguez@novatech.com, phone: 415-555-0892) now works as CTO - is pursuing a similar strategy but at about half the scale. James mentioned they're allocating roughly $1.8 billion."),
        ("EXPERT", "The key differentiator for GlobalTech is their proprietary cooling technology. They filed a patent last month (not yet published) that could reduce data center energy costs by 40%."),
        ("ANALYST", "What about the timeline for revenue impact?"),
        ("EXPERT", "GlobalTech expects the platform to generate $500M in ARR within 12 months of launch. They've already secured letters of intent from three Fortune 100 companies, though I can't name them due to NDAs. The stock is currently pricing in maybe $200M of AI-related revenue for next year, so there's significant upside if they execute."),
        ("ANALYST", "Thank you, Dr. Chen. This has been very insightful."),
    ]

    for speaker, text in conversations:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(20, 5, f"{speaker}:")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, text)
        pdf.ln(3)

    path = os.path.join(OUTPUT_DIR, "transcript-expert-call.pdf")
    pdf.output(path)
    print(f"  Created: {path}")


def create_web_article_pdf():
    """Public news article — no MNPI, no PII."""
    pdf = StyledPDF()
    pdf.add_page()
    pdf.header_block(
        "AWS Announces General Availability of Bedrock AgentCore",
        "Reuters - July 22, 2026",
    )

    pdf.body_text(
        "SEATTLE - Amazon Web Services (AWS) today announced the general availability "
        "of Amazon Bedrock AgentCore, a fully managed service that enables enterprises "
        "to deploy, scale, and govern AI agents in production environments."
    )
    pdf.ln(3)
    pdf.body_text("The service provides built-in reliability, security, and observability features including:")
    pdf.ln(2)
    pdf.body_text("  - AgentCore Runtime for serverless agent deployment")
    pdf.body_text("  - AgentCore Gateway for secure tool routing with policy enforcement")
    pdf.body_text("  - Integration with Amazon Bedrock Guardrails for content safety")
    pdf.body_text("  - Native support for the Model Context Protocol (MCP)")
    pdf.ln(3)
    pdf.body_text(
        '"Enterprises need a platform that handles the undifferentiated heavy lifting of '
        'running agents at scale," said Matt Garman, CEO of AWS. "AgentCore lets builders '
        'focus on agent logic while we handle deployment, scaling, security, and governance."'
    )
    pdf.ln(3)
    pdf.body_text(
        "The announcement comes as the enterprise AI agent market continues to grow rapidly. "
        "According to Gartner, 75% of large enterprises will have deployed at least one AI "
        "agent in production by 2027, up from less than 5% in 2024."
    )
    pdf.ln(3)
    pdf.body_text(
        "AWS also announced that AgentCore now supports Bedrock Guardrails in the Policy layer, "
        "enabling enterprises to enforce content filtering, PII detection, and prompt attack "
        "prevention at the gateway level - outside the agent's code where it cannot be circumvented."
    )
    pdf.ln(3)
    pdf.body_text(
        "AgentCore is available in US East (N. Virginia), US West (Oregon), and Europe (Ireland) "
        "regions. Pricing is based on compute usage with no minimum commitments."
    )

    path = os.path.join(OUTPUT_DIR, "web-article-public.pdf")
    pdf.output(path)
    print(f"  Created: {path}")


def create_hr_document_pdf():
    """HR onboarding record — heavy PII."""
    pdf = StyledPDF()
    pdf.add_page()
    pdf.header_block(
        "Employee Onboarding Record",
        "Human Resources Department",
        "CONFIDENTIAL - HR USE ONLY",
    )

    # Use body_text for everything to keep PDF structure simple for Textract
    pdf.section_header("Employee Details")
    pdf.body_text("Full Name: Alexandra Maria Thompson")
    pdf.body_text("Date of Birth: March 15, 1989")
    pdf.body_text("Social Security Number: 478-55-9123")
    pdf.body_text("Home Address: 247 Westfield Lane, Apt 12B, Stamford, CT 06902")
    pdf.body_text("Personal Email: alex.thompson.personal@gmail.com")
    pdf.body_text("Phone: (203) 555-0234")
    pdf.body_text("Emergency Contact: Robert Thompson (spouse) - (203) 555-0891")
    pdf.ln(5)

    pdf.section_header("Employment Details")
    pdf.body_text("Position: Senior Quantitative Analyst")
    pdf.body_text("Department: Systematic Trading")
    pdf.body_text("Start Date: August 1, 2026")
    pdf.body_text("Manager: David Park")
    pdf.body_text("Annual Salary: $425,000 base + discretionary bonus")
    pdf.body_text("Employee ID: AC-2026-4521")
    pdf.ln(5)

    pdf.section_header("Banking Information")
    pdf.body_text("Bank: Chase Bank")
    pdf.body_text("Routing Number: 021000021")
    pdf.body_text("Account Number: 8847291056")
    pdf.ln(5)

    pdf.section_header("Benefits Enrollment")
    pdf.body_text("Medical: Family Plan (Aetna PPO)")
    pdf.body_text("Dental: Individual + Spouse")
    pdf.body_text("401(k): 6% contribution, company match 4%")
    pdf.body_text("Life Insurance: 2x salary")
    pdf.ln(5)

    pdf.section_header("Background Check")
    pdf.body_text("Criminal: Clear")
    pdf.body_text("Credit: Satisfactory")
    pdf.body_text("Education Verified: PhD Mathematics, MIT (2015)")
    pdf.body_text("Previous Employment Verified: Goldman Sachs (2015-2022), Two Sigma (2022-2026)")
    pdf.ln(5)

    pdf.section_header("IT Access and Compliance Notes")
    pdf.body_text("Network Account: athompson@fsicompany.com")
    pdf.body_text("Badge ID: 889234")
    pdf.body_text("Building Access: HQ Floor 4, Trading Floor, Data Center")
    pdf.ln(3)
    pdf.body_text(
        "NOTE: Employee must complete compliance training by September 15, 2026. "
        "Access to restricted trading systems pending manager approval."
    )

    path = os.path.join(OUTPUT_DIR, "hr-document-pii.pdf")
    pdf.output(path)
    print(f"  Created: {path}")


def create_slack_export_pdf():
    """Slack channel export — internal, no MNPI."""
    pdf = StyledPDF()
    pdf.add_page()
    pdf.header_block(
        "Slack Channel Export: #research-team",
        "Exported: July 18, 2026",
        "INTERNAL",
    )

    messages = [
        ("09:15", "david.park", "Team standup - quick updates please"),
        ("09:16", "jennifer.lee", "Working on the macro model revision. Should have updated GDP forecasts by EOD. Nothing material yet, just refining seasonal adjustments."),
        ("09:17", "mark.wilson", "I've been reviewing the healthcare sector. Found some interesting public data from CMS on Medicare spending trends. Will circulate a summary this afternoon."),
        ("09:18", "david.park", "Good. @mark.wilson please make sure to flag anything that could be construed as MNPI before sharing broadly. We had an issue last quarter."),
        ("09:19", "mark.wilson", "Understood. Everything I'm working with is from public CMS databases and published academic papers. All clean."),
        ("09:20", "jennifer.lee", "Also reminder - the quarterly investment committee meeting is Thursday at 2pm. Room 4B. Agenda went out yesterday."),
        ("09:21", "david.park", "Thanks all. @jennifer.lee can you also prepare a brief on the inflation expectations model? I want to present it at IC."),
        ("09:22", "jennifer.lee", "Will do. I'll have it ready by Wednesday EOD for your review."),
        ("09:23", "david.park", "Perfect. Let's keep things moving. Have a productive day everyone."),
    ]

    for time, user, text in messages:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(12, 5, f"[{time}]")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(0, 0, 150)
        pdf.cell(28, 5, f"@{user}")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(0, 5, text)
        pdf.ln(2)

    path = os.path.join(OUTPUT_DIR, "slack-internal.pdf")
    pdf.output(path)
    print(f"  Created: {path}")


if __name__ == "__main__":
    print("Generating sample PDF documents...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    create_mnpi_email_pdf()
    create_expert_transcript_pdf()
    create_web_article_pdf()
    create_hr_document_pdf()
    create_slack_export_pdf()

    print(f"\nDone! 5 PDFs created in {OUTPUT_DIR}/")
