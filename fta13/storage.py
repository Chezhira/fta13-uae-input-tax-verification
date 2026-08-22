# Copyright (c) 2026 Chez Solutions. Authored by Zahidah Murira.
# MIT License: https://github.com/Chezhira/fta13-uae-input-tax-verification

"""Supabase persistence with user-scoped rows and private document paths."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .extraction import sha256_bytes


@dataclass
class SavedDocument:
    document_id: str
    storage_path: str
    sha256: str


class SupabaseStore:
    bucket = "fta13-documents"

    def __init__(self, url: str, anon_key: str, access_token: str, refresh_token: str):
        from supabase import create_client

        self.client = create_client(url, anon_key)
        self.client.auth.set_session(access_token, refresh_token)
        user = self.client.auth.get_user()
        if not user or not user.user:
            raise PermissionError("A valid user session is required.")
        self.user_id = user.user.id

    def save_document(
        self,
        *,
        filename: str,
        mime_type: str,
        content: bytes,
        extraction: dict[str, Any],
    ) -> SavedDocument:
        document_id = str(uuid4())
        safe_name = filename.replace("/", "_").replace("\\", "_")
        storage_path = f"{self.user_id}/{document_id}/{safe_name}"
        digest = sha256_bytes(content)
        self.client.storage.from_(self.bucket).upload(
            storage_path,
            content,
            {"content-type": mime_type, "upsert": "false"},
        )
        try:
            self.client.table("documents").insert(
                {
                    "id": document_id,
                    "user_id": self.user_id,
                    "filename": safe_name,
                    "mime_type": mime_type,
                    "size_bytes": len(content),
                    "sha256": digest,
                    "storage_path": storage_path,
                    "detected_languages": extraction.get("detected_languages", []),
                    "extraction": extraction,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ).execute()
        except Exception:
            self.client.storage.from_(self.bucket).remove([storage_path])
            raise
        return SavedDocument(document_id, storage_path, digest)

    def save_assessment(self, payload: dict[str, Any]) -> str:
        assessment_id = str(uuid4())
        safe_payload = json.loads(json.dumps(payload, default=str))
        self.client.table("assessments").insert(
            {
                "id": assessment_id,
                "user_id": self.user_id,
                "supplier_reference": safe_payload.get("supplier_reference"),
                "supply_reference": safe_payload.get("supply_reference"),
                "status": safe_payload.get("status", "open"),
                "payload": safe_payload,
            }
        ).execute()
        return assessment_id

    def list_assessments(self) -> list[dict[str, Any]]:
        result = (
            self.client.table("assessments")
            .select("id,supplier_reference,supply_reference,status,created_at,updated_at")
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        return list(result.data or [])


def request_email_otp(url: str, anon_key: str, email: str) -> None:
    from supabase import create_client

    client = create_client(url, anon_key)
    client.auth.sign_in_with_otp({"email": email})


def verify_email_otp(url: str, anon_key: str, email: str, token: str) -> dict[str, str]:
    from supabase import create_client

    client = create_client(url, anon_key)
    response = client.auth.verify_otp(
        {"email": email, "token": token, "type": "email"}
    )
    session = response.session
    if session is None:
        raise PermissionError("Supabase did not return a session for this code.")
    return {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
    }
