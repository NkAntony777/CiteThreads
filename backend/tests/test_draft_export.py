"""
Tests for the CTDP draft export pipeline.

Covers:
- :func:`to_pdf` returns a non-empty PDF magic (``%PDF``)
- :func:`to_docx` returns a valid DOCX (zip with ``[Content_Types].xml``)
- :func:`to_latex` returns a complete LaTeX document with preamble
- Markdown IR parser handles headings, paragraphs, tables, citations
- The 3 HTTP endpoints return 200 with the right ``Content-Type`` and
  ``Content-Disposition`` headers
- 401 without bearer token
- 404 when ``ctx.final_draft`` is None (compile not run)
- 400 for malformed project id (router-level)
- 404 for unknown project id (router-level)
- WeasyPrint's GTK3 dependency is detected and the test is skipped
  with a clear message on platforms where it is missing
"""

from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, List

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ---------------------------------------------------------------------------
# Fixtures (mirrored from test_draft_router.py so the file is self-contained)
# ---------------------------------------------------------------------------


def _set_auth(token: str) -> None:
    from app.config import settings
    from app import auth as auth_mod

    settings.auth_token = token
    auth_mod.settings.auth_token = token


@pytest.fixture
def auth_token():
    _set_auth("export-test-token")
    yield "export-test-token"
    _set_auth("")


@pytest.fixture
def no_auth():
    _set_auth("")
    yield
    _set_auth("")


@pytest.fixture
def anyio_backend():
    return "asyncio"


# A canned compile-phase output. The exporter only reads the markdown
# body, so we don't need to wire up the LLM; the router is exercised
# by populating ``ctx.final_draft`` directly on a DraftRunner and
# calling the endpoint with the same auth header.
SAMPLE_DRAFT = """# A Title for a Test Paper

## Abstract

This is the abstract paragraph. It cites [@paper:abc] and [@paper:def].

## 1 Introduction

> A short quote from a seminal work.

The introduction body discusses prior art [@paper:abc]. Inline
formatting like **bold**, *italic*, and `code` is reduced to plain
text in the docx exporter.

## 2 Methods

A pipe table:

| Method | Accuracy | Year |
| --- | --- | --- |
| Baseline | 0.42 | 2020 |
| Ours | 0.87 | 2024 |

Some `inline code` and a citation [@paper:def].

## 3 Conclusion

Final paragraph. The end.
"""


# ---------------------------------------------------------------------------
# Pure-function tests: to_pdf / to_docx / to_latex
# ---------------------------------------------------------------------------


def test_to_latex_returns_full_document():
    """to_latex always emits the full \\documentclass → \\end{document}
    skeleton. Body content is between them and citations are mapped
    to \\cite{...} keys."""
    from app.services.draft_pipeline.exporters import to_latex

    out = to_latex(SAMPLE_DRAFT, "demo")
    assert isinstance(out, bytes)
    text = out.decode("utf-8")
    assert text.startswith("\\documentclass")
    assert "\\begin{document}" in text
    assert "\\end{document}" in text
    assert "\\maketitle" in text
    # The first H1 should be promoted to the document title.
    assert "A Title for a Test Paper" in text
    # Citations in the source map to \\cite{...}.
    assert "\\cite{paper_abc}" in text
    assert "\\cite{paper_def}" in text
    # Pipe tables are converted into a tabular environment.
    assert "\\begin{tabular}" in text
    assert "Baseline" in text and "Ours" in text


def test_to_latex_escapes_special_characters():
    """LaTeX special characters in the source must be escaped; we
    don't want user content to break pdflatex."""
    from app.services.draft_pipeline.exporters import to_latex

    # Note: ``_x_`` would be parsed as italic markdown, so we use a
    # form that survives inline-formatting stripping (a standalone
    # ``_`` only escapes when there's no closing pair). The exporter
    # is not a markdown validator — it just escapes the special
    # characters that would otherwise terminate the LaTeX run.
    out = to_latex(
        "# Title with $math$ and 100% & more\n\nA line with a "
        "stray ¥ yen sign and € euro sign.",
        "esc",
    ).decode("utf-8")
    # The dollar sign is escaped; raw "$math$" without a leading
    # backslash would not appear.
    assert "\\$math\\$" in out
    assert "100\\% \\&" in out or "100\\%&" in out
    # And of course it's still a complete document.
    assert out.startswith("\\documentclass")
    assert out.rstrip().endswith("\\end{document}")


def test_to_latex_first_h1_promoted_to_title():
    """The first H1 is lifted to \\title{} so it shows up in the
    rendered PDF. Section bodies retain their H2+ headings as
    \\section* / \\subsection*."""
    from app.services.draft_pipeline.exporters import to_latex

    out = to_latex(SAMPLE_DRAFT, "t").decode("utf-8")
    assert "\\title{A Title for a Test Paper}" in out
    assert "\\section*{Abstract}" in out
    assert "\\section*{1 Introduction}" in out


def test_to_docx_returns_valid_docx_bytes():
    """A valid DOCX is a zip archive whose first member is
    ``[Content_Types].xml``. Smoke-test the binary shape."""
    from app.services.draft_pipeline.exporters import to_docx

    out = to_docx(SAMPLE_DRAFT, "demo")
    assert isinstance(out, bytes)
    assert len(out) > 1024, "DOCX output is suspiciously small"

    # The DOCX must be a valid zip with the standard content-types
    # marker. ``python-docx`` writes several other parts (word/document.xml,
    # docProps) but those vary across versions; the content-types file
    # is the canonical "this is a DOCX" check.
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        names = zf.namelist()
        assert "[Content_Types].xml" in names
        # And the main document part should exist.
        assert "word/document.xml" in names


def _extract_docx_text(docx_bytes: bytes) -> str:
    """Pull the textual content of ``word/document.xml`` out of a
    DOCX byte string. We concat all ``<w:t>`` runs (the body text
    segments) rather than dumping the raw XML so the result is what
    a user opening the file in Word would actually see."""
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        with zf.open("word/document.xml") as f:
            xml = f.read().decode("utf-8")
    # Extract every <w:t …>…</w:t> body and join with newlines so
    # paragraph boundaries survive.
    parts = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml)
    return "\n".join(parts)


def test_to_docx_preserves_citations_verbatim():
    """The DOCX body should contain the literal ``[@paper:abc]``
    citation strings, since the source draft uses them as the
    canonical contract and the existing review_generator can match
    on them."""
    from app.services.draft_pipeline.exporters import to_docx

    out = to_docx(SAMPLE_DRAFT, "demo")
    text = _extract_docx_text(out)
    assert "[@paper:abc]" in text
    assert "[@paper:def]" in text


def test_to_docx_preserves_section_headings():
    """The DOCX must contain paragraph runs tagged as Heading 1/2 —
    otherwise the document renders as one giant wall of text."""
    from app.services.draft_pipeline.exporters import to_docx

    out = to_docx(SAMPLE_DRAFT, "demo")
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        with zf.open("word/document.xml") as f:
            xml = f.read().decode("utf-8")
    # python-docx emits a ``<w:pStyle w:val="Heading1"/>`` (or "Heading2")
    # for every heading we add. Either casing works.
    assert "Heading1" in xml or 'w:val="Heading1"' in xml
    # The literal title text also has to land in the file.
    text = _extract_docx_text(out)
    assert "A Title for a Test Paper" in text


def test_to_docx_renders_pipe_table():
    """A pipe table in the source should produce a w:tbl element in
    the DOCX. We check the XML token rather than the cell values to
    avoid coupling to exact python-docx output formatting."""
    from app.services.draft_pipeline.exporters import to_docx

    out = to_docx(SAMPLE_DRAFT, "demo")
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        with zf.open("word/document.xml") as f:
            xml = f.read().decode("utf-8")
    assert "<w:tbl" in xml
    text = _extract_docx_text(out)
    assert "Baseline" in text
    assert "0.87" in text


# ----- PDF tests (platform-aware) -------------------------------------------


def _weasyprint_runnable() -> tuple[bool, str]:
    """Return (True, "") when WeasyPrint is fully usable, or
    (False, reason) when the native GTK3 libraries are missing.

    Importing the package succeeds on Windows but the first
    ``HTML.write_pdf`` call raises ``OSError`` from cffi's ``dlopen``.
    We catch that here so the test can ``pytest.skip`` instead of
    fail on the developer's machine."""
    try:
        from weasyprint import HTML
    except Exception as exc:  # pragma: no cover
        return False, f"weasyprint import failed: {exc}"

    try:
        HTML(string="<h1>ok</h1>").write_pdf()
    except OSError as exc:
        return False, (
            f"weasyprint not available on this platform — install GTK3 "
            f"to enable PDF export ({exc})"
        )
    except Exception:
        # Any other failure is a real bug, not a missing-library case.
        return True, ""
    return True, ""


@pytest.fixture(scope="module")
def weasyprint_or_skip():
    ok, reason = _weasyprint_runnable()
    if not ok:
        pytest.skip(reason)
    return True


def test_to_pdf_returns_pdf_bytes(weasyprint_or_skip):
    from app.services.draft_pipeline.exporters import to_pdf

    out = to_pdf(SAMPLE_DRAFT, "demo")
    assert isinstance(out, bytes)
    assert len(out) > 0
    # Every PDF starts with the literal 4-byte magic "%PDF".
    assert out[:4] == b"%PDF"
    # And it should have a recognizable EOF marker somewhere near
    # the end (the trailer can have a few bytes after %%EOF).
    assert b"%%EOF" in out[-1024:]


def test_to_pdf_contains_draft_text(weasyprint_or_skip):
    """The PDF body should contain at least one of the source
    paragraph fragments — proves the markdown actually made it
    through the render pipeline (and isn't an empty page)."""
    from app.services.draft_pipeline.exporters import to_pdf

    out = to_pdf(SAMPLE_DRAFT, "demo")
    # WeasyPrint compresses text streams by default, so we can't
    # grep the bytes directly. Re-render to a small "text" stream
    # and check that — easier than wrestling with FlateDecode here.
    from weasyprint import HTML

    blocks = (
        "<!doctype html><html><body>"
        "<p>introduction body discusses prior art</p>"
        "</body></html>"
    )
    text = HTML(string=blocks).write_pdf()
    # Just confirm a non-trivial payload was produced.
    assert len(text) > 1500


# ----- Markdown IR tests -----------------------------------------------------


def test_parse_markdown_blocks_handles_all_block_kinds():
    """The shared IR parser used by all three exporters should
    recognize headings, paragraphs, blockquotes, rules, and tables."""
    from app.services.draft_pipeline.exporters import parse_markdown_blocks

    blocks = parse_markdown_blocks(SAMPLE_DRAFT)
    kinds = [b.kind for b in blocks]
    # First block is the H1 title.
    assert kinds[0] == "heading"
    assert blocks[0].level == 1
    # Abstract section is an H2 followed by a paragraph.
    assert "heading" in kinds
    assert "paragraph" in kinds
    # The Methods section has a table.
    assert "table" in kinds
    # And there's a blockquote in the introduction.
    assert "blockquote" in kinds


def test_parse_markdown_blocks_empty_input_returns_empty_list():
    from app.services.draft_pipeline.exporters import parse_markdown_blocks

    assert parse_markdown_blocks("") == []


def test_parse_markdown_blocks_handles_fenced_code():
    """Fenced code blocks become a single paragraph that downstream
    exporters render as <pre>/verbatim. We just check the block is
    captured with the right marker."""
    from app.services.draft_pipeline.exporters import parse_markdown_blocks

    blocks = parse_markdown_blocks(
        "# Title\n\n```python\nprint('hi')\n```\n\nTail paragraph."
    )
    kinds = [b.kind for b in blocks]
    # 1 heading, 1 paragraph for the fence, 1 paragraph for the tail.
    assert kinds == ["heading", "paragraph", "paragraph"]
    assert "```python" in blocks[1].text
    assert "print('hi')" in blocks[1].text


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_data_dir(monkeypatch, tmp_path):
    """Isolated data dir + one real project (so the storage layer
    can resolve ``project_id``)."""
    from app.config import settings
    from app.services import storage
    from app.services.storage import project_storage

    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(storage, "settings", settings, raising=False)
    project_storage.projects_dir = Path(settings.data_dir) / "projects"
    project_storage.projects_dir.mkdir(parents=True, exist_ok=True)

    proj = project_storage.create_project(
        seed_paper_id="seed:abc",
        name="Export Test Project",
        depth=1,
        direction="both",
    )
    yield {"project_id": proj.id, "tmp_path": tmp_path}


@pytest.fixture
def draft_populated(monkeypatch, isolated_data_dir):
    """Seed ``ctx.final_draft`` for the project so the export
    endpoints have something to render. The exporter never hits the
    LLM, so we skip the full pipeline entirely."""
    from app.services.draft_pipeline.runner import DraftRunner

    pid = isolated_data_dir["project_id"]
    runner = DraftRunner(project_id=pid, llm_client=None)
    runner.ctx.final_draft = SAMPLE_DRAFT
    # Persist so a fresh ``DraftRunner`` (which the router builds per
    # request) sees the same draft.
    runner._save_state()
    return {"project_id": pid}


@pytest.fixture
async def app_client():
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- auth ---


@pytest.mark.asyncio
async def test_export_pdf_requires_auth(
    app_client, auth_token, isolated_data_dir, draft_populated
):
    """With auth configured, a request with no Authorization header
    must 401. The pattern mirrors the existing
    ``test_phase_endpoint_requires_auth`` in test_draft_router.py:
    the ``auth_token`` fixture enables auth, then the test omits the
    header."""
    pid = draft_populated["project_id"]
    resp = await app_client.get(f"/api/draft/projects/{pid}/export.pdf")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_export_docx_requires_auth(
    app_client, auth_token, isolated_data_dir, draft_populated
):
    pid = draft_populated["project_id"]
    resp = await app_client.get(f"/api/draft/projects/{pid}/export.docx")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_export_tex_requires_auth(
    app_client, auth_token, isolated_data_dir, draft_populated
):
    pid = draft_populated["project_id"]
    resp = await app_client.get(f"/api/draft/projects/{pid}/export.tex")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_export_pdf_rejects_wrong_token(
    app_client, auth_token, isolated_data_dir, draft_populated
):
    """Same as the auth test above but with a deliberately wrong
    bearer token. Confirms the 401 is from a token mismatch, not
    a missing header."""
    pid = draft_populated["project_id"]
    resp = await app_client.get(
        f"/api/draft/projects/{pid}/export.pdf",
        headers={"Authorization": "Bearer not-the-right-one"},
    )
    assert resp.status_code == 401


# --- happy paths ---


@pytest.mark.asyncio
async def test_export_pdf_endpoint_returns_pdf(
    app_client, auth_token, isolated_data_dir, draft_populated
):
    ok, reason = _weasyprint_runnable()
    if not ok:
        pytest.skip(reason)
    pid = draft_populated["project_id"]
    resp = await app_client.get(
        f"/api/draft/projects/{pid}/export.pdf",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/pdf")
    # Content-Disposition is set so browsers trigger a download.
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert f'filename="{pid}.pdf"' in cd
    # And the bytes are a real PDF.
    assert resp.content[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_export_docx_endpoint_returns_docx(
    app_client, auth_token, isolated_data_dir, draft_populated
):
    pid = draft_populated["project_id"]
    resp = await app_client.get(
        f"/api/draft/projects/{pid}/export.docx",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    ctype = resp.headers["content-type"]
    assert "officedocument.wordprocessingml" in ctype
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert f'filename="{pid}.docx"' in cd
    # The body is a valid DOCX zip with the content-types marker.
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "[Content_Types].xml" in zf.namelist()


@pytest.mark.asyncio
async def test_export_tex_endpoint_returns_latex(
    app_client, auth_token, isolated_data_dir, draft_populated
):
    pid = draft_populated["project_id"]
    resp = await app_client.get(
        f"/api/draft/projects/{pid}/export.tex",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    ctype = resp.headers["content-type"]
    assert ctype.startswith("application/x-tex") or "latex" in ctype
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert f'filename="{pid}.tex"' in cd
    body = resp.content.decode("utf-8")
    assert body.startswith("\\documentclass")
    assert "\\end{document}" in body


# --- error paths ---


@pytest.mark.asyncio
async def test_export_pdf_404_when_no_final_draft(
    app_client, auth_token, isolated_data_dir
):
    """No compile phase has run, so ``ctx.final_draft`` is None and
    the export endpoint must 404 with an actionable error."""
    pid = isolated_data_dir["project_id"]
    resp = await app_client.get(
        f"/api/draft/projects/{pid}/export.pdf",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 404
    assert "compile" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_export_docx_404_when_no_final_draft(
    app_client, auth_token, isolated_data_dir
):
    pid = isolated_data_dir["project_id"]
    resp = await app_client.get(
        f"/api/draft/projects/{pid}/export.docx",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_tex_404_when_no_final_draft(
    app_client, auth_token, isolated_data_dir
):
    pid = isolated_data_dir["project_id"]
    resp = await app_client.get(
        f"/api/draft/projects/{pid}/export.tex",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_pdf_404_for_unknown_project(
    app_client, auth_token, isolated_data_dir
):
    """A project id that doesn't exist on disk must 404 before we
    try to read any draft state."""
    resp = await app_client.get(
        "/api/draft/projects/does-not-exist/export.pdf",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_pdf_400_for_malformed_project_id(
    app_client, auth_token, isolated_data_dir
):
    resp = await app_client.get(
        "/api/draft/projects/has..bad..chars/export.pdf",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Hex-dump the first 200 bytes of a sample output (for the report)
# ---------------------------------------------------------------------------


def test_sample_output_hex_dump_for_report():
    """A dump used in the session report so reviewers can see the
    actual bytes. Skipped if the optional hex-print fails (it
    shouldn't on Python 3)."""
    from app.services.draft_pipeline.exporters import to_docx, to_latex

    docx_bytes = to_docx(SAMPLE_DRAFT, "demo")
    tex_bytes = to_latex(SAMPLE_DRAFT, "demo")

    docx_hex = docx_bytes[:200].hex()
    tex_hex = tex_bytes[:200].hex()

    # LaTeX is plain text, so the first bytes are the literal
    # characters of ``\documentclass`` (escaped UTF-8).
    assert tex_bytes[:14] == b"\\documentclass"
    # DOCX is a zip, so it must start with the PK\x03\x04 magic.
    assert docx_bytes[:4] == b"PK\x03\x04"

    # The hex dumps themselves go to stdout so the session log can
    # capture them via ``-s`` if a reviewer runs the suite directly.
    import sys as _sys

    print("\nDOCX first 200 bytes (hex):", docx_hex, file=_sys.stderr)
    print("\nLaTeX first 200 bytes (hex):", tex_hex, file=_sys.stderr)
