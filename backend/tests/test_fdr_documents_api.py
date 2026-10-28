"""FDR documents: upload, list, metadata, download and delete.

Runs on SQLite and, when TEST_DATABASE_URL is set, on the dedicated PostgreSQL test database (the
race tests need real row locks and are PostgreSQL-only). Every file is written to a pytest temporary
directory through an injected store; nothing here can touch backend/storage or the development
database. All documents are synthetic byte strings.
"""

import hashlib
import json
import re
import uuid
from urllib.parse import quote

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.documents import get_document_store
from app.audit import verify_chain
from app.documents.storage import DocumentStore, StorageError
from app.documents.validation import sanitize_filename, sniff_content_type
from app.main import app
from app.models import FDRDocument
from tests.api_support import API, audit_events, audit_rows, build_client, make_settings, sign_in
from tests.fdr_support import sqlite_decimal_warning
from tests.test_fdr_review_api import decide, submitted
from tests.test_fdr_write_api import (  # noqa: F401  (fixtures are used by name)
    create,
    force_status,
    institution_user,
    pg_world,
    row,
    run_together,
    world,
)

pytestmark = sqlite_decimal_warning

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
CATEGORIES = ["FDR_CERTIFICATE", "SECURITY_DEPOSIT_PROOF", "SUPPORTING_LETTER", "OTHER"]


@pytest.fixture
def store(tmp_path):
    return DocumentStore(tmp_path / "private")


@pytest.fixture(autouse=True)
def _private_store(store):
    app.dependency_overrides[get_document_store] = lambda: store
    yield


def files_on_disk(store):
    return sorted(p for p in store.root.rglob("*") if p.is_file())


def stored_files(store):
    return sorted(p for p in store.documents.rglob("*") if p.is_file()) if store.documents.exists() else []


def up(who, fdr_id, data=PDF, name="advice.pdf", *, category="FDR_CERTIFICATE", ctype=None, headers=None):
    h = {**who.headers, "X-Filename": quote(name, safe="")}
    if ctype is not None:
        h["Content-Type"] = ctype
    h.update(headers or {})
    return who.client.post(f"{API}/fdrs/{fdr_id}/documents?category={category}", content=data, headers=h)


def docs(engine, fdr_id=None):
    with Session(engine) as s:
        q = select(FDRDocument).order_by(FDRDocument.uploaded_at, FDRDocument.id)
        if fdr_id:
            q = q.where(FDRDocument.fdr_id == uuid.UUID(str(fdr_id)))
        return list(s.scalars(q))


def draft_with_owner(engine, world, inst="A", **overrides):
    owner = institution_user(engine, world, inst)
    return owner, create(owner, world, **overrides)


def uploaded(owner, fdr, **kw):
    r = up(owner, fdr["id"], **kw)
    assert r.status_code == 201, r.text
    return r.json()


def delete(who, fdr_id, doc_id):
    return who.client.delete(f"{API}/fdrs/{fdr_id}/documents/{doc_id}", headers=who.headers)


# ---- successful upload ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "data", "content_type"),
    [("advice.pdf", PDF, "application/pdf"), ("scan.jpg", JPEG, "image/jpeg"),
     ("scan.JPEG", JPEG, "image/jpeg"), ("receipt.png", PNG, "image/png")],
)
def test_a_valid_document_is_stored_and_its_metadata_persisted(engine, world, store, name, data, content_type):
    owner, fdr = draft_with_owner(engine, world)
    r = up(owner, fdr["id"], data, name, category="SECURITY_DEPOSIT_PROOF", ctype=content_type)
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"id", "fdr_id", "category", "original_filename", "content_type", "size_bytes",
                         "sha256", "uploaded_by_name", "uploaded_at"}  # no storage key, ever
    assert body["category"] == "SECURITY_DEPOSIT_PROOF" and body["original_filename"] == name
    assert body["content_type"] == content_type and body["size_bytes"] == len(data)
    assert body["sha256"] == hashlib.sha256(data).hexdigest()

    (doc,) = docs(engine)
    assert str(doc.id) == body["id"] and str(doc.fdr_id) == fdr["id"] and doc.uploaded_by == owner.user_id
    assert doc.status == "ACTIVE" and doc.deleted_at is None
    assert re.fullmatch(r"[0-9a-f]{32}", doc.storage_key)
    (path,) = stored_files(store)
    assert path == store.path_for(doc.storage_key) and path.read_bytes() == data
    assert files_on_disk(store) == [path]  # nothing left in the incoming area


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_category_is_accepted(engine, world, category):
    owner, fdr = draft_with_owner(engine, world)
    assert up(owner, fdr["id"], category=category).json()["category"] == category


def test_uploading_does_not_change_the_fdr_record_or_invalidate_the_open_edit_form(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    uploaded(owner, fdr)
    assert row(engine, fdr["id"]).row_version == fdr["row_version"]
    assert owner.patch(f"/fdrs/{fdr['id']}", {"row_version": fdr["row_version"], "purpose": "still editable"}).status_code == 200


def test_the_uploader_is_the_authenticated_user_not_something_the_client_says(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    r = up(owner, fdr["id"], headers={"X-Uploaded-By": str(uuid.uuid4())})
    assert r.status_code == 201 and docs(engine)[0].uploaded_by == owner.user_id


def test_a_colleague_in_the_same_institution_can_add_to_the_record(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    colleague = institution_user(engine, world, "A")
    assert up(colleague, fdr["id"]).status_code == 201
    assert docs(engine)[0].uploaded_by == colleague.user_id


# ---- listing, metadata, download ------------------------------------------------------------
def test_the_owner_lists_and_reads_metadata_in_upload_order(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    first = uploaded(owner, fdr, name="one.pdf")
    second = uploaded(owner, fdr, data=PNG, name="two.png", category="OTHER")
    listing = owner.get(f"/fdrs/{fdr['id']}/documents")
    assert listing.status_code == 200 and listing.headers["cache-control"] == "no-store"
    assert [d["id"] for d in listing.json()["items"]] == [first["id"], second["id"]]
    one = owner.get(f"/fdrs/{fdr['id']}/documents/{first['id']}")
    assert one.status_code == 200 and one.json() == first


def test_download_returns_the_exact_bytes_as_a_safe_attachment(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr, name="Résumé of deposit.pdf")
    r = owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content")
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment;") and "filename*=UTF-8''R%C3%A9sum%C3%A9%20of%20deposit.pdf" in disposition
    assert 'filename="R_sum_ of deposit.pdf"' in disposition
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_the_stored_type_is_the_verified_one_not_the_extension_alone(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    uploaded(owner, fdr, data=JPEG, name="photo.jpeg")
    assert docs(engine)[0].content_type == "image/jpeg"


@pytest.mark.parametrize("role", ["AICTE_REVIEWER", "AICTE_APPROVER"])
def test_reviewers_and_approvers_can_read_documents_of_a_submitted_record(engine, world, role):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    assert owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 1}).status_code == 200
    reviewer = sign_in(engine, role=role)
    assert [d["id"] for d in reviewer.get(f"/fdrs/{fdr['id']}/documents").json()["items"]] == [doc["id"]]
    assert reviewer.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}").json()["original_filename"] == "advice.pdf"
    r = reviewer.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content")
    assert r.status_code == 200 and r.content == PDF
    (event,) = audit_events(engine, "document.download", "success")
    assert event.actor_role == role


def test_reviewers_still_see_documents_after_a_decision(engine, world):
    owner, fdr = submitted(engine, world)  # submitted without documents; add via a correction round
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    assert decide(reviewer, "request-correction", fdr).status_code == 200
    doc = uploaded(owner, fdr, name="added.pdf")
    resubmit = owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": fdr["row_version"] + 1})
    assert resubmit.status_code == 200
    approver = sign_in(engine, role="AICTE_APPROVER")
    assert decide(approver, "accept", resubmit.json()).status_code == 200
    assert [d["id"] for d in approver.get(f"/fdrs/{fdr['id']}/documents").json()["items"]] == [doc["id"]]


def test_the_institution_reads_its_own_documents_in_every_status(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    for status in ["SUBMITTED", "CORRECTION_REQUESTED", "ACCEPTED", "REJECTED", "WITHDRAWN"]:
        force_status(engine, fdr["id"], status)
        assert owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content").status_code == 200, status


# ---- rejected files -------------------------------------------------------------------------
def assert_rejected(engine, store, who, fdr, response, status, reason):
    assert response.status_code == status, response.text
    assert docs(engine) == [] and files_on_disk(store) == []  # nothing stored, nothing left behind
    (event,) = audit_events(engine, "document.upload", "denied")
    assert event.details["reason"] == reason and event.details["target_fdr_id"] == fdr["id"]
    assert not audit_events(engine, "document.upload", "success")


@pytest.mark.parametrize("name", ["tool.exe", "page.html", "image.svg", "bundle.zip", "notes.txt", "letter.docx",
                                  "script.js", "archive.tar.gz", "noextension", "advice.pdf.exe", ".pdf", "advice.pdf "])
def test_unsupported_extensions_are_rejected(engine, world, store, name):
    owner, fdr = draft_with_owner(engine, world)
    reason = "extension_not_allowed"
    r = up(owner, fdr["id"], PDF, name)
    if name == "advice.pdf ":  # trailing space is trimmed away, so this is simply a PDF
        assert r.status_code == 201
        return
    assert_rejected(engine, store, owner, fdr, r, 415, reason)


@pytest.mark.parametrize(
    ("name", "data", "reason"),
    [
        ("report.pdf", b"MZ\x90\x00\x03\x00\x00\x00 not a pdf", "signature_not_allowed"),  # executable
        ("scan.png", b"<html><script>alert(1)</script></html>", "signature_not_allowed"),
        ("logo.png", b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>", "signature_not_allowed"),
        ("bundle.pdf", b"PK\x03\x04\x14\x00\x00\x00 zip", "signature_not_allowed"),
        ("note.jpg", b"just text pretending to be an image", "signature_not_allowed"),
        ("pdf-ish.pdf", b"%PDF-x not a version", "signature_not_allowed"),
        ("truncated.png", b"\x89PNG\r\n\x1a\n", "signature_not_allowed"),  # no IHDR chunk
        ("photo.pdf", PNG, "extension_mismatch"),  # real image, wrong extension
        ("doc.png", PDF, "extension_mismatch"),
        ("shot.jpg", PNG, "extension_mismatch"),
    ],
)
def test_the_file_signature_must_match_a_permitted_type_and_the_extension(engine, world, store, name, data, reason):
    owner, fdr = draft_with_owner(engine, world)
    assert_rejected(engine, store, owner, fdr, up(owner, fdr["id"], data, name), 415, reason)


@pytest.mark.parametrize("declared", ["image/png", "text/html", "application/x-msdownload", "image/svg+xml"])
def test_a_spoofed_declared_mime_type_is_rejected(engine, world, store, declared):
    owner, fdr = draft_with_owner(engine, world)
    assert_rejected(engine, store, owner, fdr, up(owner, fdr["id"], PDF, "advice.pdf", ctype=declared), 415, "content_type_mismatch")


@pytest.mark.parametrize("declared", ["application/octet-stream", "application/pdf", "application/pdf; charset=binary", "APPLICATION/PDF"])
def test_a_missing_generic_or_matching_declared_type_is_fine(engine, world, declared):
    owner, fdr = draft_with_owner(engine, world)
    assert up(owner, fdr["id"], PDF, "advice.pdf", ctype=declared).status_code == 201


def test_an_empty_file_is_rejected(engine, world, store):
    owner, fdr = draft_with_owner(engine, world)
    assert_rejected(engine, store, owner, fdr, up(owner, fdr["id"], b"", "advice.pdf"), 422, "empty_file")


def test_a_missing_file_name_is_rejected(engine, world, store):
    owner, fdr = draft_with_owner(engine, world)
    r = owner.client.post(f"{API}/fdrs/{fdr['id']}/documents?category=OTHER", content=PDF, headers=owner.headers)
    assert_rejected(engine, store, owner, fdr, r, 422, "filename_missing")


def test_an_unknown_or_missing_category_is_rejected_before_anything_is_stored(engine, world, store):
    owner, fdr = draft_with_owner(engine, world)
    for query in ["category=PASSPORT", "category=", ""]:
        r = owner.client.post(f"{API}/fdrs/{fdr['id']}/documents?{query}", content=PDF,
                              headers={**owner.headers, "X-Filename": "a.pdf"})
        assert r.status_code == 422
    assert docs(engine) == [] and files_on_disk(store) == []


def test_an_oversized_file_is_rejected_by_declared_length_and_by_bytes_actually_sent(engine, world, store):
    owner = sign_in(engine, role="INSTITUTION_USER", institution_id=world["A"], settings=make_settings(document_max_bytes=2048))
    fdr = create(owner, world)
    big = PDF + b"0" * 2048
    r = up(owner, fdr["id"], big)  # Content-Length says it is too big
    assert r.status_code == 413
    # No Content-Length at all (chunked): the limit applies to what is actually received.
    r = up(owner, fdr["id"], (big[i:i + 500] for i in range(0, len(big), 500)))
    assert r.status_code == 413
    assert docs(engine) == [] and files_on_disk(store) == []
    exactly = PDF + b"0" * (2048 - len(PDF))
    assert up(owner, fdr["id"], exactly).status_code == 201  # the limit itself is allowed


def test_a_streamed_upload_is_stored_when_it_is_valid(engine, world, store):
    owner, fdr = draft_with_owner(engine, world)
    chunks = (PDF[i:i + 7] for i in range(0, len(PDF), 7))
    assert up(owner, fdr["id"], chunks).status_code == 201
    assert stored_files(store)[0].read_bytes() == PDF


def test_an_fdr_cannot_hold_more_than_the_configured_number_of_documents(engine, world, store):
    owner = sign_in(engine, role="INSTITUTION_USER", institution_id=world["A"], settings=make_settings(document_max_per_fdr=2))
    fdr = create(owner, world)
    first = uploaded(owner, fdr, name="1.pdf")
    uploaded(owner, fdr, name="2.pdf")
    r = up(owner, fdr["id"], name="3.pdf")
    assert r.status_code == 409 and "at most 2" in r.json()["detail"]
    assert len(stored_files(store)) == 2 and files_on_disk(store) == stored_files(store)
    assert audit_events(engine, "document.upload", "denied")[0].details["reason"] == "document_limit_reached"
    assert delete(owner, fdr["id"], first["id"]).status_code == 204  # deleting frees a slot
    assert up(owner, fdr["id"], name="4.pdf").status_code == 201


# ---- filenames, paths and storage keys ------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("..\\..\\windows\\system32\\evil.pdf", "evil.pdf"),
        ("C:\\Users\\x\\advice.pdf", "advice.pdf"),
        ("/absolute/path/advice.pdf", "advice.pdf"),
        ("advice\x00.pdf.exe", "advice.pdf.exe"),
        ("ad\u202evice\u202d.pdf", "advice.pdf"),  # bidi override characters removed
        ("a<b>c:d\"e|f?g*h.pdf", "a_b_c_d_e_f_g_h.pdf"),
        ("  spaced   out  .pdf", "spaced out .pdf"),
        ("..pdf", "pdf"),
        ("", "document"),
        ("a" * 400 + ".pdf", "a" * 146 + ".pdf"),
    ],
)
def test_filename_sanitising(raw, expected):
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("name", ["../../escape.pdf", "..\\..\\escape.pdf", "/etc/cron.d/x.pdf", "nul.pdf", "con.pdf"])
def test_hostile_file_names_never_influence_where_a_file_is_stored(engine, world, store, name):
    owner, fdr = draft_with_owner(engine, world)
    assert up(owner, fdr["id"], PDF, name).status_code == 201
    (doc,) = docs(engine)
    (path,) = stored_files(store)
    assert path.parent.parent == store.documents and path.name == doc.storage_key
    assert path.is_relative_to(store.root)
    assert "escape" not in str(path) and "etc" not in path.relative_to(store.root).parts
    assert "/" not in doc.original_filename and "\\" not in doc.original_filename
    assert not (store.root.parent / "escape.pdf").exists()


def test_storage_keys_are_random_unique_and_unrelated_to_the_upload(engine, world, store):
    owner, fdr = draft_with_owner(engine, world)
    for _ in range(3):
        uploaded(owner, fdr, name="same-name.pdf")  # identical name and bytes must never collide
    keys = [d.storage_key for d in docs(engine)]
    assert len(set(keys)) == 3 and all(re.fullmatch(r"[0-9a-f]{32}", k) for k in keys)
    assert len(stored_files(store)) == 3 and not any("same-name" in str(p) for p in files_on_disk(store))


@pytest.mark.parametrize("key", ["../../etc/passwd", "..", "a" * 31, "A" * 32, "g" * 32, "0" * 31 + "/", "", "0" * 33, "..\\" + "0" * 30])
def test_the_store_refuses_anything_but_a_generated_key(store, key):
    with pytest.raises(StorageError):
        store.path_for(key)
    with pytest.raises(StorageError):
        store.read(key)


def test_the_store_never_overwrites_an_existing_file(store):
    key = store.new_key()
    first = store.new_incoming_path()
    first.write_bytes(b"first")
    store.promote(first, key)
    second = store.new_incoming_path()
    second.write_bytes(b"second")
    with pytest.raises(StorageError):
        store.promote(second, key)
    assert store.read(key) == b"first"


def test_sniffing_recognises_only_the_three_supported_formats():
    assert sniff_content_type(PDF[:16]) == "application/pdf"
    assert sniff_content_type(JPEG[:16]) == "image/jpeg"
    assert sniff_content_type(PNG[:16]) == "image/png"
    for other in [b"", b"<!DOCTYPE html>", b"<?xml version", b"GIF89a", b"PK\x03\x04", b"MZ", b"#!/bin/sh\n", b"\x7fELF"]:
        assert sniff_content_type(other) is None


def test_storage_stays_outside_every_static_route(engine, world, store):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    key = docs(engine)[0].storage_key
    for path in [f"/storage/documents/{key[:2]}/{key}", f"/documents/{key}", f"/static/{key}", f"/uploads/{key}",
                 f"{API}/documents/{doc['id']}", f"{API}/storage/{key}"]:
        assert owner.client.get(path).status_code == 404, path
    assert key not in json.dumps(owner.get(f"/fdrs/{fdr['id']}/documents").json())


# ---- workflow state --------------------------------------------------------------------------
@pytest.mark.parametrize("status", ["DRAFT", "CORRECTION_REQUESTED"])
def test_documents_can_be_added_and_removed_while_the_institution_may_edit(engine, world, status):
    owner, fdr = draft_with_owner(engine, world)
    force_status(engine, fdr["id"], status)
    doc = uploaded(owner, fdr)
    assert delete(owner, fdr["id"], doc["id"]).status_code == 204


@pytest.mark.parametrize("status", ["SUBMITTED", "ACCEPTED", "REJECTED", "WITHDRAWN"])
def test_the_document_set_is_frozen_once_the_record_has_left_the_institution(engine, world, store, status):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    force_status(engine, fdr["id"], status)
    r = up(owner, fdr["id"], name="late.pdf")
    assert r.status_code == 409 and "draft or has been returned" in r.json()["detail"]
    assert delete(owner, fdr["id"], doc["id"]).status_code == 409
    assert [d.original_filename for d in docs(engine)] == ["advice.pdf"] and len(files_on_disk(store)) == 1
    assert {e.details["reason"] for e in audit_events(engine, "document.upload", "denied")} == {"invalid_status"}
    assert audit_events(engine, "document.delete", "denied")


def test_a_refused_upload_does_not_even_write_the_body_to_disk(engine, world, store):
    owner, fdr = draft_with_owner(engine, world)
    force_status(engine, fdr["id"], "SUBMITTED")
    assert up(owner, fdr["id"]).status_code == 409
    assert not store.incoming.exists() or list(store.incoming.iterdir()) == []


def test_the_round_trip_through_review_keeps_documents_attached(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    uploaded(owner, fdr, name="first.pdf")
    assert owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 1}).status_code == 200
    assert up(owner, fdr["id"], name="too-late.pdf").status_code == 409
    reviewer = sign_in(engine, role="AICTE_REVIEWER")
    assert decide(reviewer, "request-correction", {"id": fdr["id"], "row_version": 2}).status_code == 200
    uploaded(owner, fdr, name="second.pdf")
    names = [d["original_filename"] for d in owner.get(f"/fdrs/{fdr['id']}/documents").json()["items"]]
    assert names == ["first.pdf", "second.pdf"]


# ---- deletion --------------------------------------------------------------------------------
def test_deleting_hides_the_document_and_erases_the_file_of_a_never_submitted_draft(engine, world, store):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    r = delete(owner, fdr["id"], doc["id"])
    assert r.status_code == 204 and r.content == b""
    assert owner.get(f"/fdrs/{fdr['id']}/documents").json()["items"] == []
    assert owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}").status_code == 404
    assert owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content").status_code == 404
    (stored,) = docs(engine)  # the row is kept, marked deleted
    assert stored.status == "DELETED" and stored.deleted_at is not None and stored.deleted_by == owner.user_id
    assert stored_files(store) == []
    assert delete(owner, fdr["id"], doc["id"]).status_code == 404  # not repeatable
    (event,) = audit_events(engine, "document.delete", "success")
    assert event.entity_type == "document" and event.entity_id == doc["id"]


def test_deleting_after_a_review_round_keeps_the_bytes_the_reviewer_may_have_seen(engine, world, store):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    assert owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 1}).status_code == 200
    assert decide(sign_in(engine, role="AICTE_REVIEWER"), "request-correction", {"id": fdr["id"], "row_version": 2}).status_code == 200
    assert delete(owner, fdr["id"], doc["id"]).status_code == 204
    assert docs(engine)[0].status == "DELETED" and len(stored_files(store)) == 1
    assert owner.get(f"/fdrs/{fdr['id']}/documents").json()["items"] == []


def test_a_document_id_from_another_fdr_does_not_resolve(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    other = create(owner, world, fdr_number="OTHER-1")
    doc = uploaded(owner, fdr)
    for method, suffix in [("get", ""), ("get", "/content")]:
        assert getattr(owner, method)(f"/fdrs/{other['id']}/documents/{doc['id']}{suffix}").status_code == 404
    assert delete(owner, other["id"], doc["id"]).status_code == 404
    assert docs(engine)[0].status == "ACTIVE"


# ---- isolation and roles -------------------------------------------------------------------
def test_another_institution_cannot_see_or_touch_the_documents(engine, world, store):
    owner, fdr = draft_with_owner(engine, world, "A")
    doc = uploaded(owner, fdr)
    intruder = institution_user(engine, world, "B")
    missing = uuid.uuid4()
    for r in [
        intruder.get(f"/fdrs/{fdr['id']}/documents"),
        intruder.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}"),
        intruder.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content"),
        up(intruder, fdr["id"], name="planted.pdf"),
        delete(intruder, fdr["id"], doc["id"]),
    ]:
        assert r.status_code == 404 and r.json() == {"detail": "FDR not found"}
    # indistinguishable from a record that does not exist
    assert up(intruder, str(missing)).json() == {"detail": "FDR not found"}
    assert [d.original_filename for d in docs(engine)] == ["advice.pdf"] and len(files_on_disk(store)) == 1
    assert len(audit_events(engine, "authz.scope_denied", "denied")) == 6
    assert not audit_events(engine, "document.download", "success")


READ_ROLES = {"INSTITUTION_USER", "AICTE_REVIEWER", "AICTE_APPROVER"}
ALL_ROLES = ["INSTITUTION_USER", "AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"]


def who(engine, world, role, owner_inst="A"):
    return institution_user(engine, world, owner_inst) if role == "INSTITUTION_USER" else sign_in(engine, role=role)


@pytest.mark.parametrize("role", ALL_ROLES)
def test_read_access_follows_the_role_policy(engine, world, role):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    assert owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 1}).status_code == 200
    actor = who(engine, world, role)
    statuses = [actor.get(f"/fdrs/{fdr['id']}/documents").status_code,
                actor.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}").status_code,
                actor.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content").status_code]
    assert statuses == ([200] * 3 if role in READ_ROLES else [403] * 3)
    if role not in READ_ROLES:  # refusals are audited and nothing was served
        assert len(audit_events(engine, "authz.denied", "denied")) == 3
        assert not audit_events(engine, "document.download", "success")


@pytest.mark.parametrize("role", ["AICTE_REVIEWER", "AICTE_APPROVER"])
def test_reviewers_cannot_see_documents_of_a_draft(engine, world, role):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    reviewer = sign_in(engine, role=role)
    for path in ["", f"/{doc['id']}", f"/{doc['id']}/content"]:
        assert reviewer.get(f"/fdrs/{fdr['id']}/documents{path}").status_code == 404


@pytest.mark.parametrize("role", ["AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN", "AUDITOR"])
def test_only_institution_users_can_upload_or_delete(engine, world, store, role):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 1})
    actor = sign_in(engine, role=role)
    assert up(actor, fdr["id"], name="x.pdf").status_code == 403
    assert delete(actor, fdr["id"], doc["id"]).status_code == 403
    assert len(docs(engine)) == 1 and len(files_on_disk(store)) == 1


def test_unauthenticated_requests_are_refused(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    anonymous = build_client(engine)  # carries a trusted Origin but no session cookie
    base = f"{API}/fdrs/{fdr['id']}/documents"
    assert anonymous.get(base).status_code == 401
    assert anonymous.get(f"{base}/{doc['id']}/content").status_code == 401
    assert anonymous.post(f"{base}?category=OTHER", content=PDF, headers={"X-Filename": "a.pdf"}).status_code == 401
    assert anonymous.delete(f"{base}/{doc['id']}").status_code == 401


@pytest.mark.parametrize("headers", [{}, {"X-CSRF-Token": "forged"}])
def test_uploads_and_deletes_need_a_valid_csrf_token(engine, world, store, headers):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    h = {"X-Filename": "a.pdf", **headers}
    assert owner.client.post(f"{API}/fdrs/{fdr['id']}/documents?category=OTHER", content=PDF, headers=h).status_code == 403
    assert owner.client.delete(f"{API}/fdrs/{fdr['id']}/documents/{doc['id']}", headers=headers).status_code == 403
    assert len(docs(engine)) == 1 and len(files_on_disk(store)) == 1 and docs(engine)[0].status == "ACTIVE"
    assert audit_events(engine, "security.csrf_rejected", "denied")


def test_uploads_from_an_untrusted_origin_are_refused(engine, world, store):
    owner, fdr = draft_with_owner(engine, world)
    r = up(owner, fdr["id"], headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and docs(engine) == [] and files_on_disk(store) == []


def test_a_pending_password_change_blocks_every_document_route(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    from app.models import User
    with Session(engine) as s:
        s.get(User, owner.user_id).must_change_password = True
        s.commit()
    assert owner.get(f"/fdrs/{fdr['id']}/documents").status_code == 403
    assert owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content").status_code == 403
    assert up(owner, fdr["id"], name="b.pdf").status_code == 403
    assert delete(owner, fdr["id"], doc["id"]).status_code == 403


# ---- missing files, integrity, and partial failures ---------------------------------------
def test_a_missing_file_is_reported_without_leaking_anything_and_is_audited(engine, world, store):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    stored_files(store)[0].unlink()
    r = owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content")
    assert r.status_code == 404 and r.json() == {"detail": "The stored file for this document is unavailable"}
    assert audit_events(engine, "document.download", "denied")[0].details["reason"] == "file_missing"
    assert owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}").status_code == 200  # metadata still readable
    assert not audit_events(engine, "document.download", "success")


def test_a_file_that_no_longer_matches_its_checksum_is_never_served(engine, world, store):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)
    stored_files(store)[0].write_bytes(PDF.replace(b"Catalog", b"Tampere"))  # same size, different bytes
    r = owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content")
    assert r.status_code == 404
    assert audit_events(engine, "document.download", "denied")[0].details["reason"] == "integrity_check_failed"
    stored_files(store)[0].write_bytes(PDF + b"extra")
    assert owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content").status_code == 404


def test_a_storage_failure_on_download_is_a_clean_404(engine, world, store, monkeypatch):
    owner, fdr = draft_with_owner(engine, world)
    doc = uploaded(owner, fdr)

    def broken(_key):
        raise StorageError("disk unavailable")

    monkeypatch.setattr(store, "read", broken)
    assert owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content").status_code == 404
    assert audit_events(engine, "document.download", "denied")[0].details["reason"] == "storage_error"


def test_a_failure_moving_the_file_leaves_no_metadata_and_no_files(engine, world, store, monkeypatch):
    owner, fdr = draft_with_owner(engine, world)

    def broken(_incoming, _key):
        raise StorageError("disk full")

    monkeypatch.setattr(store, "promote", broken)
    r = up(owner, fdr["id"])
    assert r.status_code == 503 and "could not be stored" in r.json()["detail"]
    assert docs(engine) == [] and files_on_disk(store) == []
    assert not audit_events(engine, "document.upload", "success")  # the audit row rolled back with it
    monkeypatch.undo()
    assert up(owner, fdr["id"]).status_code == 201  # the next attempt is unaffected


def test_a_database_failure_at_commit_removes_the_file_that_was_just_moved(engine, world, store, monkeypatch):
    owner, fdr = draft_with_owner(engine, world)
    real_commit = Session.commit
    seen = {}

    def failing_commit(self):
        if stored_files(store):  # the file is already in place: this is the commit that matters
            seen["file_existed"] = True
            raise OperationalError("COMMIT", {}, Exception("connection lost"))
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", failing_commit)
    r = up(owner, fdr["id"])
    monkeypatch.setattr(Session, "commit", real_commit)
    assert r.status_code == 503 and seen == {"file_existed": True}
    assert docs(engine) == [] and files_on_disk(store) == []
    assert not audit_events(engine, "document.upload", "success")


def test_a_failure_while_writing_the_audit_event_stores_nothing(engine, world, store, monkeypatch):
    owner, fdr = draft_with_owner(engine, world)
    import app.api.documents as module

    def broken(*_a, **_k):
        raise OperationalError("INSERT", {}, Exception("audit unavailable"))

    monkeypatch.setattr(module, "write_audit_event", broken)
    assert up(owner, fdr["id"]).status_code == 503
    assert docs(engine) == [] and files_on_disk(store) == []


def test_an_unexpected_error_after_the_move_also_cleans_up(engine, world, store, monkeypatch):
    owner = sign_in(engine, role="INSTITUTION_USER", institution_id=world["A"], raise_server_exceptions=False)
    fdr = create(owner, world)
    real_promote = store.promote

    def promote_then_explode(incoming, key):
        real_promote(incoming, key)
        raise RuntimeError("something unforeseen")

    monkeypatch.setattr(store, "promote", promote_then_explode)
    assert up(owner, fdr["id"]).status_code == 500
    assert docs(engine) == [] and files_on_disk(store) == []


# ---- audit ------------------------------------------------------------------------------------
def test_audit_events_describe_the_action_without_any_file_content_or_name(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    secret_name = "Aadhaar 1234 5678 9012 - Jayachandra.pdf"
    doc = uploaded(owner, fdr, name=secret_name, category="SUPPORTING_LETTER")
    owner.get(f"/fdrs/{fdr['id']}/documents/{doc['id']}/content")
    delete(owner, fdr["id"], doc["id"])
    up(owner, fdr["id"], b"<html>", "bad.pdf")

    events = [e for e in audit_rows(engine) if e.action.startswith("document.")]
    assert [(e.action, e.outcome) for e in events] == [
        ("document.upload", "success"), ("document.download", "success"),
        ("document.delete", "success"), ("document.upload", "denied"),
    ]
    upload = events[0]
    assert upload.entity_type == "document" and upload.entity_id == doc["id"] and upload.actor_user_id == owner.user_id
    assert upload.details == {"target_fdr_id": fdr["id"], "category": "SUPPORTING_LETTER",
                              "size_bytes": len(PDF), "content_type": "application/pdf"}
    dump = json.dumps([[e.details, e.entity_id, e.action] for e in audit_rows(engine)])
    for forbidden in [secret_name, "Aadhaar", "Jayachandra", hashlib.sha256(PDF).hexdigest(),
                      docs(engine)[0].storage_key, "%PDF", "<html>"]:
        assert forbidden not in dump
    with Session(engine) as s:
        assert verify_chain(s).ok


def test_the_fdr_revision_snapshot_never_carries_document_data(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    uploaded(owner, fdr, name="evidence.pdf")
    assert owner.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 1}).status_code == 200
    from tests.test_fdr_write_api import revisions
    snapshot = json.dumps(revisions(engine, fdr["id"])[0].snapshot)
    assert "evidence" not in snapshot and "document" not in snapshot.lower()


# ---- the registry/review responses are unchanged ------------------------------------------
def test_existing_fdr_responses_do_not_grow_document_fields(engine, world):
    owner, fdr = draft_with_owner(engine, world)
    uploaded(owner, fdr)
    assert "documents" not in owner.get(f"/fdrs/{fdr['id']}").json()


# ---- concurrency (PostgreSQL only) --------------------------------------------------------
def test_an_upload_racing_a_submission_is_either_kept_whole_or_refused_whole(pg_head, pg_world, tmp_path):
    pg_store = DocumentStore(tmp_path / "race")
    app.dependency_overrides[get_document_store] = lambda: pg_store
    for round_no in range(6):
        uploader, submitter = (institution_user(pg_head, pg_world, email=f"docrace{round_no}-{i}@example.invalid") for i in range(2))
        fdr = create(uploader, pg_world, fdr_number=f"DOC-RACE-{round_no}")
        app.dependency_overrides[get_document_store] = lambda: pg_store
        upload_status, submit_status = run_together([
            lambda: up(uploader, fdr["id"], name=f"race-{round_no}.pdf").status_code,
            lambda: submitter.post(f"/fdrs/{fdr['id']}/submit", {"row_version": 1}).status_code,
        ])
        assert submit_status == 200 and upload_status in (201, 409), (upload_status, submit_status)
        attached = docs(pg_head, fdr["id"])
        assert len(attached) == (1 if upload_status == 201 else 0)
        for d in attached:  # a document that was accepted is really on disk; a refused one left no trace
            assert pg_store.path_for(d.storage_key).read_bytes() == PDF
    assert len(stored_files(pg_store)) == len(docs(pg_head))
    assert list(pg_store.incoming.iterdir()) == []
    with Session(pg_head) as s:
        assert verify_chain(s).ok


def test_simultaneous_uploads_respect_the_document_limit(pg_head, pg_world, tmp_path):
    pg_store = DocumentStore(tmp_path / "limit")
    app.dependency_overrides[get_document_store] = lambda: pg_store
    settings = make_settings(document_max_per_fdr=3)
    actors = [sign_in(pg_head, role="INSTITUTION_USER", institution_id=pg_world["A"], settings=settings,
                      email=f"limit-{i}@example.invalid") for i in range(6)]
    fdr = create(actors[0], pg_world)
    app.dependency_overrides[get_document_store] = lambda: pg_store
    statuses = run_together([lambda a=a, i=i: up(a, fdr["id"], name=f"n{i}.pdf").status_code for i, a in enumerate(actors)])
    assert sorted(statuses) == [201] * 3 + [409] * 3, statuses
    assert len(docs(pg_head, fdr["id"])) == 3 and len(stored_files(pg_store)) == 3
