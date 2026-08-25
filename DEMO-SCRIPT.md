# Demo Script: Agentic Data Classification & Redaction

This document provides test prompts for each demo user persona, with expected responses and the reasoning behind each behavior. Use this to walk a customer through the bidirectional enforcement model.

---

## Key Concepts to Demonstrate

1. **Partial redaction** — Documents are never fully blocked if the user has security clearance. Only the sensitive portions (PII values, MNPI paragraphs) are surgically redacted inline while the rest remains readable.
2. **Same document, different views** — The same content renders differently per user based on their MNPI wall-crossing and PII access entitlements.
3. **Security level gating** — Users below the document's security level cannot access it at all and don't even know it exists.
4. **Transparent enforcement** — The system always tells the user *what* was redacted and *why*.
5. **Full pipeline from PDF** — Documents are uploaded as PDFs, text is extracted via Textract, classified by Bedrock Claude, embedded by Titan Embeddings V2, and indexed (full text + vector + metadata) to OpenSearch Serverless k-NN. At query time, everything is served from OpenSearch — no S3 in the hot path.

---

## Demo Users

| User | Role | Security Level | MNPI Cleared | PII Access |
|------|------|---------------|--------------|------------|
| Alice Chen | Portfolio Manager | Restricted | ACME Corp, TechStart Inc., GlobalTech, NovaTech | No |
| Bob Martinez | Research Analyst | Restricted | ACME Corp, TechStart Inc. | No |
| Carol Davis | Compliance Officer | Restricted | All entities | Yes |
| Dave Wilson | Summer Intern | Internal | None | No |
| Eve Johnson | HR Manager | Restricted | None | Yes |

---

## Sample Documents

| Document | Security Level | MNPI | MNPI Entities | PII Types |
|----------|---------------|------|---------------|-----------|
| email-mnpi-acme.pdf | Restricted | Yes | ACME Corp, TechStart Inc. | email_address, phone_number |
| transcript-expert-call.pdf | Restricted | Yes | GlobalTech Industries, NovaTech Systems | email_address, phone_number, name |
| web-article-public.pdf | Public | No | — | — |
| hr-document-pii.pdf | Confidential | No | — | ssn, email_address, phone_number, address, financial_account |
| slack-internal.pdf | Internal | No | — | — |

---

## Scenario 1: Full MNPI Access with PII Redaction

**User:** Alice Chen (Portfolio Manager)  
**Prompt:** `Show me any documents related to upcoming earnings announcements`

**Expected Response:**
- Returns the ACME Corp email
- All MNPI content is visible: Q3 revenue ($4.2B), buyback ($2B), TechStart acquisition ($890M), guidance revision (8-12%)
- Email addresses show as `{EMAIL}`
- Phone number shows as `{PHONE}`
- Footer note: "MNPI present (you are cleared) | PII redacted (no PII access)"

**Why:** Alice is wall-crossed for ACME Corp and TechStart Inc., so all the deal intelligence flows through unredacted. But she has no business need for personal contact details (sender's direct phone line, email addresses), so those are masked. This demonstrates that MNPI clearance and PII access are independent controls — you can be cleared for financial secrets but still have personal data withheld.

---

## Scenario 2: MNPI Paragraph Redaction (Not Cleared for Entity)

**User:** Bob Martinez (Research Analyst)  
**Prompt:** `Can you find any research or calls mentioning GlobalTech?`

**Expected Response:**
- Returns the expert call transcript
- Entire paragraphs where the expert discusses GlobalTech's financials are replaced with `[MNPI REDACTED - not cleared for GlobalTech Industries]`
- Paragraphs about NovaTech are replaced with `[MNPI REDACTED - not cleared for NovaTech Systems]`
- The analyst's general questions remain visible (they don't contain MNPI themselves)
- Expert's email and phone are shown as `{EMAIL}` / `{PHONE}`
- Footer: "MNPI redacted for: GlobalTech Industries, NovaTech Systems | PII redacted (no PII access)"

**Why:** Bob is only cleared for ACME Corp and TechStart. The expert call contains undisclosed financial details about GlobalTech ($3.5B capex, $500M ARR projection) and NovaTech ($1.8B allocation) that Bob hasn't been wall-crossed for. The system lets him see that an expert call happened and the general topic, but surgically removes the material financial details he's not entitled to. This is paragraph-level redaction, not line-level — if "GlobalTech" appears anywhere in the expert's answer, the entire answer block is redacted since the context around it is equally sensitive.

**Contrast — same prompt as Alice:**  
`Can you find any research or calls mentioning GlobalTech?`  
Alice sees the full transcript with all MNPI visible (she's cleared for GlobalTech and NovaTech). Only PII (email, phone) remains masked. Same document, different view.

---

## Scenario 3: Security Level Block (Insufficient Clearance)

**User:** Dave Wilson (Summer Intern)  
**Prompt:** `I need to review any earnings-related communications`

**Expected Response:**
- Message: "No documents found matching 'earnings' within your clearance level (Internal)."
- No documents returned at all

**Why:** The ACME earnings email is classified as "Restricted" — Dave only has "Internal" clearance. The system doesn't reveal that the document exists, doesn't tell Dave it was blocked, doesn't mention what he's missing. From Dave's perspective, there simply are no matching documents. This is the strongest form of access control: you can't leak what you don't know about.

**Follow-up prompt:** `What's happening in the research team Slack channel?`

**Expected Response:**
- Returns the Slack channel export in full
- No redaction of any kind — full content visible
- No footer notes about redaction

**Why:** The Slack export is classified as "Internal" which matches Dave's clearance level. It contains no MNPI and no redactable PII (just people's names, which aren't pattern-redacted). This proves the system isn't over-blocking — Dave CAN access appropriate content. The system correctly distinguishes between what's above his clearance and what's within it.

---

## Scenario 4: Full Unrestricted Access (Compliance Officer)

**User:** Carol Davis (Compliance Officer)  
**Prompt:** `Show me any documents related to upcoming earnings announcements`

**Expected Response:**
- Returns the ACME Corp email in full
- ALL content visible including MNPI details AND personal information
- Email addresses (john.smith@fsicompany.com) fully visible
- Phone numbers ((203) 555-0147) fully visible
- No redaction whatsoever
- No footer notes

**Why:** Carol has full MNPI clearance (all entities) AND PII access. Compliance officers need unredacted access for investigations — they must be able to see exactly what information flowed, to whom, and what personal details are in play. This is the most privileged view in the system. The same document that shows 6+ redaction markers for Bob shows zero for Carol.

**Follow-up prompt:** `Pull up the HR onboarding record for the new hire`

**Expected Response:**
- Returns the HR document in full
- SSN (478-55-9123), home address, bank account numbers, salary — all visible
- Email, phone, emergency contact — all visible
- No redaction of any kind

**Why:** Carol's PII access means she can see the most sensitive personal data. This demonstrates that the system supports full pass-through for appropriately entitled personnel.

---

## Scenario 5: PII-Heavy Document with Inline Masking

**User:** Bob Martinez (Research Analyst)  
**Prompt:** `Show me the employee onboarding record`

**Expected Response:**
- Returns the HR document content
- SSN shows as `{US_SOCIAL_SECURITY_NUMBER}`
- Phone numbers show as `{PHONE}`
- Email addresses show as `{EMAIL}`
- Home address shows as `{ADDRESS}`
- Bank account number shows as `[ACCOUNT REDACTED]`
- Employee name, job title, department, salary, benefits — visible (contextual info preserved)
- No MNPI redaction (HR doc is classified as no MNPI)
- Footer: "PII redacted (no PII access)"

**Why:** This demonstrates dense PII redaction. The document has 7+ distinct PII values scattered throughout, and each one is independently detected and masked with its type label. Bob can still understand the document's purpose (new analyst onboarding, their role, their compensation structure) but can't extract personal identity information. The redaction is surgical — it removes the values, not the context.

**Contrast — same prompt as Eve (HR Manager):**  
Eve has PII access but NO MNPI clearance. Since the HR doc has no MNPI, she sees everything unredacted — SSN, address, bank details all visible. She has the same full view as Carol for this particular document.

---

## Scenario 6: Public Content (No Restrictions)

**User:** Dave Wilson (Summer Intern)  
**Prompt:** `What did AWS announce about their agent platform?`

**Expected Response:**
- Returns the web article about AWS Bedrock AgentCore
- Full content visible, no redaction of any kind
- Security level: Public
- No footer notes

**Why:** Public content flows through to everyone regardless of clearance level. This demonstrates that the classification system correctly identifies non-sensitive content and applies no restrictions. The system adds value in both directions — protecting sensitive data AND ensuring clean data flows freely without unnecessary friction.

---

## Scenario 7: Semantic Search (Finding by Meaning, Not Keywords)

**User:** Alice Chen (Portfolio Manager)  
**Prompt:** `Are there any documents discussing share buyback programs?`

**Expected Response:**
- Finds and returns the ACME email (the text contains "$2B share buyback program")
- Shows the full email content with PII redacted

**Why:** The search uses semantic vector matching (Titan Embeddings V2 → OpenSearch Serverless k-NN), not keyword lookup. "Buyback programs" as a concept matches the document even though the user didn't type the exact phrasing from the PDF. This eliminates the brittleness of keyword search — users can describe what they're looking for in natural language and the system finds it by meaning.

**More semantic search examples:**
- `Do we have any notes about GPU cluster infrastructure?` → finds the expert call transcript (discusses "GPU cluster management platform")
- `What are the latest discussions about Medicare spending data?` → finds the Slack messages (mentions "CMS on Medicare spending trends")
- `Show me any documents that mention employee compensation` → finds the HR document (contains salary details, even though "compensation" doesn't appear literally)
- `What undisclosed financial projections do we have?` → finds both the ACME email and expert call (both contain undisclosed financial figures)

---

## Scenario 8: Side-by-Side Comparison (Same Document, Two Users)

**User:** Bob Martinez (Research Analyst)  
**Prompt:** `Show me the expert network call transcript we received recently`

Then switch to **Alice Chen** and ask: `Show me the expert network call transcript we received recently`

**Expected Difference:**

| Content Section | Bob Sees | Alice Sees |
|----------------|----------|------------|
| Analyst's opening question | Visible | Visible |
| Expert reveals GlobalTech's $3.5B capex plan | `[MNPI REDACTED - not cleared for GlobalTech Industries]` | Full text visible |
| Analyst asks about competitors | Visible | Visible |
| Expert reveals NovaTech's $1.8B allocation | `[MNPI REDACTED - not cleared for NovaTech Systems]` | Full text visible |
| Expert's contact info (email, phone) | `{EMAIL}` / `{PHONE}` | `{EMAIL}` / `{PHONE}` |
| Expert discusses revenue timeline | `[MNPI REDACTED - not cleared for GlobalTech Industries]` | Full text visible |
| General market commentary | Visible | Visible |

**Why:** Both users get PII masked (neither has PII access). But MNPI redaction differs per wall-crossing. Alice sees the full financial intelligence because she's cleared for those entities. Bob sees the conversation structure (he knows an expert call about AI infrastructure happened, he can see the analyst's questions) but the specific material financial details are surgically removed. This is the strongest demo of "same infrastructure, per-user views" — no data duplication, just entitlement-driven rendering.

---

## Scenario 9: Multi-Turn Conversation (Follow-Up Questions)

**User:** Alice Chen (Portfolio Manager)  
**Prompt 1:** `Show me any documents related to upcoming earnings announcements`

**Expected:** Returns the ACME Corp email with MNPI visible, PII masked.

**Prompt 2 (follow-up):** `Who sent that email?`

**Expected:** The agent recalls the previously retrieved ACME email from session memory and answers: "John Smith, Senior Portfolio Manager" (with contact details redacted as PII).

**Prompt 3 (follow-up):** `What acquisitions are mentioned in it?`

**Expected:** Agent references the same email context and responds about the TechStart Inc. acquisition ($890M, expected to close by end of August).

**Why:** AgentCore Memory (short-term) maintains conversation history within the session. The agent doesn't re-search — it refers back to context from earlier messages. This enables natural, conversational interaction without the user needing to repeat context or re-specify documents.

**Contrast — switch to Bob, ask the same follow-up:**  
After switching to Bob (new session), asking "Who sent that email?" returns nothing useful because Bob has a fresh session with no prior context. The session reset on user change ensures one user's conversation doesn't bleed into another's.

---

## Summary: What Each User Sees Across All Documents

| Document | Alice (PM) | Bob (Analyst) | Carol (Compliance) | Dave (Intern) | Eve (HR) |
|----------|-----------|--------------|-------------------|--------------|---------|
| ACME Email (Restricted, MNPI) | MNPI visible, PII masked | MNPI visible, PII masked | Everything visible | **NOT VISIBLE** | MNPI redacted (all entities), PII visible |
| Expert Call (Restricted, MNPI) | MNPI visible, PII masked | MNPI redacted (GlobalTech/NovaTech), PII masked | Everything visible | **NOT VISIBLE** | MNPI redacted (all entities), PII visible |
| HR Document (Confidential, PII) | PII masked | PII masked | Everything visible | **NOT VISIBLE** | Everything visible |
| Slack Messages (Internal) | Full content | Full content | Full content | Full content | Full content |
| Web Article (Public) | Full content | Full content | Full content | Full content | Full content |

---

## Talking Points for Customer

1. **"Classification happens once at ingestion, enforcement happens at query time."**  
   The pipeline classifies content asynchronously when PDFs are uploaded (Textract → Bedrock Claude → Titan Embeddings → OpenSearch k-NN). At query time, the agent invokes tools through the AgentCore Gateway, where Guardrails and entitlement logic enforce redaction. No latency on the classification side at query time.

2. **"The agent is the intelligence layer — tools provide data, the agent reasons."**  
   The agent calls tools to search and retrieve content. Tools return redacted data (MNPI filtered, PII suppressed). The agent then reasons over that data: summarizing, answering questions, comparing documents. The agent never sees raw sensitive data.

3. **"Semantic search, not keyword matching."**  
   Users ask natural language questions — "what undisclosed projections do we have?" — and the agent's search tool finds relevant documents by meaning using vector similarity (Titan Embeddings + OpenSearch k-NN). No brittle keyword lists, no exact-match limitations.

4. **"Multi-turn conversation with memory."**  
   The agent remembers what was discussed within a session. Ask about earnings, then follow up with "who sent that?" — the agent refers back to the prior context. Session resets when the user identity changes, ensuring conversation isolation between users.

5. **"Same infrastructure, per-user views."**  
   No data duplication. One classified document, one vector in OpenSearch. The agent passes user identity to tools, and tools apply entitlement-based redaction per request. Different users see different views of the same content.

6. **"Fail-closed by default."**  
   If content hasn't been classified, tools deny access. If a user's clearance is below the document's security level, the document doesn't appear in search results. The agent can only work with what tools return.

7. **"Transparent, auditable decisions."**  
   Every redaction is explained inline. The agent acknowledges when content is redacted and explains why. Compliance can reconstruct what any user saw at any time.

8. **"Partial redaction preserves utility."**  
   Documents aren't blocked wholesale. The agent receives a document with sensitive paragraphs replaced by `[MNPI REDACTED]` markers — the rest of the content is fully usable for reasoning, summarization, and Q&A.
