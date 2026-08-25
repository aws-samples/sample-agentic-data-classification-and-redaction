"""
Text Extraction Lambda
Triggered by EventBridge when content is uploaded to the S3 landing bucket.
Extracts text from various formats and sends to the classification queue.
"""

import json
import os
import uuid
import re
from datetime import datetime, timezone
from zipfile import ZipFile
from xml.etree import ElementTree
from io import BytesIO

import boto3

s3 = boto3.client("s3")
sqs = boto3.client("sqs")
textract = boto3.client("textract")

PROCESSED_BUCKET = os.environ["PROCESSED_BUCKET"]
CLASSIFICATION_QUEUE_URL = os.environ["CLASSIFICATION_QUEUE_URL"]


def lambda_handler(event, context):
    """Process S3 upload event and extract text content."""
    detail = event.get("detail", {})
    bucket = detail.get("bucket", {}).get("name")
    key = detail.get("object", {}).get("key")

    if not bucket or not key:
        print(f"Invalid event: {json.dumps(event)}")
        return {"statusCode": 400, "body": "Invalid event"}

    print(f"Processing: s3://{bucket}/{key}")

    # Parse source metadata from key: raw/{source_type}/{date}/{filename}
    key_parts = key.split("/")
    source_type = key_parts[1] if len(key_parts) > 1 else "unknown"
    filename = key_parts[-1]
    content_id = str(uuid.uuid4())

    # Download the file
    response = s3.get_object(Bucket=bucket, Key=key)
    file_bytes = response["Body"].read()
    content_type = response.get("ContentType", "")

    # Extract text based on file type
    extracted_text = extract_text(file_bytes, filename, content_type, bucket, key)

    if not extracted_text or not extracted_text.strip():
        print(f"No text extracted from {key}")
        return {"statusCode": 200, "body": "No text extracted"}

    # Store extracted text in processed bucket
    processed_key = f"extracted/{content_id}.json"
    processed_payload = {
        "content_id": content_id,
        "source_type": source_type,
        "source_key": key,
        "filename": filename,
        "extracted_text": extracted_text,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "text_length": len(extracted_text),
    }

    s3.put_object(
        Bucket=PROCESSED_BUCKET,
        Key=processed_key,
        Body=json.dumps(processed_payload),
        ContentType="application/json",
    )

    # Send to classification queue
    sqs.send_message(
        QueueUrl=CLASSIFICATION_QUEUE_URL,
        MessageBody=json.dumps(
            {
                "content_id": content_id,
                "processed_key": processed_key,
                "source_type": source_type,
                "filename": filename,
            }
        ),
    )

    print(
        f"Extracted {len(extracted_text)} chars from {filename}, "
        f"content_id={content_id}"
    )

    return {
        "statusCode": 200,
        "body": json.dumps(
            {"content_id": content_id, "text_length": len(extracted_text)}
        ),
    }


def extract_text(file_bytes, filename, content_type, bucket, key):
    """Extract text from various file formats."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if ext == "pdf" or content_type == "application/pdf":
        return extract_from_pdf(bucket, key)
    elif ext == "docx":
        return extract_from_docx(file_bytes)
    elif ext in ("html", "htm") or "html" in content_type:
        return extract_from_html(file_bytes.decode("utf-8", errors="replace"))
    elif ext == "json" or content_type == "application/json":
        return extract_from_json(file_bytes.decode("utf-8", errors="replace"))
    elif ext in ("txt", "md", "csv", "eml") or "text/" in content_type:
        return file_bytes.decode("utf-8", errors="replace")
    else:
        # Try as plain text
        try:
            return file_bytes.decode("utf-8", errors="replace")
        except Exception:
            return extract_from_pdf(file_bytes)


def extract_from_pdf(bucket, key):
    """Use Textract to extract text from a PDF stored in S3."""
    try:
        response = textract.detect_document_text(
            Document={
                "S3Object": {
                    "Bucket": bucket,
                    "Name": key,
                }
            }
        )
        lines = []
        for block in response.get("Blocks", []):
            if block["BlockType"] == "LINE":
                lines.append(block.get("Text", ""))
        text = "\n".join(lines)
        if text.strip():
            return text
        print("Textract returned empty text, falling back to raw extraction")
        return extract_pdf_fallback(bucket, key)
    except Exception as e:
        print(f"Textract extraction failed: {e}, trying fallback")
        return extract_pdf_fallback(bucket, key)


def extract_pdf_fallback(bucket, key):
    """Fallback PDF text extraction using raw stream parsing."""
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        pdf_bytes = response["Body"].read()
        # Simple extraction: find text between BT/ET markers or stream content
        import re as re_mod
        text_parts = []
        # Extract text from PDF streams (works for simple PDFs from fpdf2)
        streams = re_mod.findall(rb'stream\r?\n(.*?)\r?\nendstream', pdf_bytes, re_mod.DOTALL)
        for stream in streams:
            try:
                # Try to decode as text
                decoded = stream.decode("latin-1", errors="ignore")
                # Extract text between Tj/TJ operators
                tj_matches = re_mod.findall(r'\((.*?)\)\s*Tj', decoded)
                text_parts.extend(tj_matches)
                # Also try TJ arrays
                tj_array = re_mod.findall(r'\[(.*?)\]\s*TJ', decoded)
                for arr in tj_array:
                    parts = re_mod.findall(r'\((.*?)\)', arr)
                    text_parts.extend(parts)
            except Exception:
                continue
        return "\n".join(text_parts) if text_parts else ""
    except Exception as e:
        print(f"Fallback PDF extraction also failed: {e}")
        return ""


def extract_from_docx(file_bytes):
    """Extract text from DOCX files."""
    try:
        import defusedxml.ElementTree as SafeET
        with ZipFile(BytesIO(file_bytes)) as z:
            with z.open("word/document.xml") as f:
                tree = SafeET.parse(f)
                root = tree.getroot()

        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = root.findall(".//w:p", ns)
        text_content = []
        for p in paragraphs:
            runs = p.findall(".//w:t", ns)
            para_text = "".join(r.text for r in runs if r.text)
            if para_text.strip():
                text_content.append(para_text.strip())

        return "\n".join(text_content)
    except Exception as e:
        print(f"DOCX extraction failed: {e}")
        return ""


def extract_from_html(html_content):
    """Strip HTML tags and extract text."""
    # Remove script and style tags
    clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", " ", clean)
    # Normalize whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def extract_from_json(json_content):
    """Extract text fields from JSON content."""
    try:
        data = json.loads(json_content)
        texts = []
        _extract_strings(data, texts)
        return "\n".join(texts)
    except json.JSONDecodeError:
        return json_content


def _extract_strings(obj, texts):
    """Recursively extract string values from a JSON object."""
    if isinstance(obj, str) and len(obj) > 10:
        texts.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_strings(v, texts)
    elif isinstance(obj, list):
        for item in obj:
            _extract_strings(item, texts)
