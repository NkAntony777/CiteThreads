"""Tests for the validate and compile phases.

Covers:
- Referee agent: produces qa_report, identifies narrative issues
- FactCheck: detects orphan `[@paper_id]` citations, lists verified
- Compiler: assembles final_draft from section_drafts, includes QA
- Orchestrators: mark phases done, attach quality scores
- Failure paths: mark phases FAILED on exception
"""

from __future__ import annotations

import json

import pytest

from app.services.draft_pipeline import (
    CitationStyle,
    DraftContext,
    PhaseName,
    PhaseStatus,
)
from app.services.draft_pipeline.phases import (
    CompilerResult,
    FactCheckResult,
    FactCheckClaim,
    RefereeFinding,
    RefereeResult,
    abstract_writer,
    compile,
    compiler,
    factcheck,
    referee,
    run_compile_phase,
    run_validate_phase,
    validate,
)
from app.services.draft_pipeline.phases.compile import (
    _assemble_body,
    _collect_paper_metadata,
    _compose_final,
    _derive_qa_summary,
    _format_authors,
    _format_reference_line,
    _format_qa_header,
    _heuristic_abstract,
    _ordered_section_names,
    _referenced_paper_ids,
    _render_references_markdown,
)
from app.services.draft_pipeline.phases.validate import (
    _all_section_text,
    _deterministic_factcheck,
    _extract_citation_ids,
    _extract_section_name,
    _format_factcheck_block,
    _known_paper_ids,
    _parse_referee_markdown,
)


# ---------------------------------------------------------------------------
# Mocks (shared pattern across the test suite)
# ---------------------------------------------------------------------------


class _MockChoice:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()


class _MockResponse:
    def __init__(self, content: str):
        self.choices = [_MockChoice(content)]


class _MockCompletions:
    def __init__(self, scripted):
        self._scripted = list(scripted) if not isinstance(scripted, str) else [scripted]
        self.calls: list[dict] = []

    async def create(self, *, model, messages, temperature, max_tokens):
        self.calls.append(
            {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        )
        if not self._scripted:
            raise RuntimeError("MockCompletions ran out of scripted responses")
        return _MockResponse(self._scripted.pop(0))


class _MockLLMClient:
    def __init__(self, scripted):
        self.chat = type("Chat", (), {"completions": _MockCompletions(scripted)})()


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_ctx_with_sections() -> DraftContext:
    """A DraftContext with a representative set of section drafts,
    paper summaries, and known paper IDs. This is the minimum state
    a downstream Compile phase would have after Compose."""
    ctx = DraftContext(
        project_id="p1",
        topic="Neural networks for drug discovery",
        language="en",
        citation_style=CitationStyle.APA,
        target_word_count=6000,
        reference_ids=["ref1", "ref2"],
        graph_node_ids=["g1", "g2", "g3"],
        paper_summaries=[
            {
                "paper_id": "openalex:W100",
                "title": "Deep learning for molecular property prediction",
                "authors": ["Alice Smith", "Bob Jones"],
                "year": 2023,
                "venue": "Nature Machine Intelligence",
                "doi": "10.1038/example.1234",
            },
            {
                "paper_id": "arxiv:2401.00001",
                "title": "Graph neural networks for drug-target interaction",
                "authors": ["Carol Lee"],
                "year": 2024,
                "venue": "arXiv preprint",
                "doi": "10.48550/example.5678",
            },
            {
                "paper_id": "s2:hashA",
                "title": "Survey of deep learning in computational chemistry",
                "authors": ["Dan Kim", "Eve Brown"],
                "year": 2022,
                "venue": "J. Chem. Inf. Model.",
            },
        ],
        section_drafts={
            "introduction": (
                "Drug discovery is expensive. [@openalex:W100] argue that "
                "machine learning can reduce the cost. We explore this in "
                "this review. [@ref1] provide an overview."
            ),
            "literature_review": (
                "Many surveys exist. [@arxiv:2401.00001] is the most recent. "
                "[@s2:hashA] is a 2022 survey. We contrast their findings."
            ),
            "methodology": (
                "We followed PRISMA. [@ref2] describes a similar method."
            ),
            "results": (
                "We found 12 relevant papers. [@openalex:W100] is the most "
                "cited, and [@not_in_corpus] is an orphan citation that "
                "should be flagged by FactCheck."
            ),
            "discussion": (
                "Limitations include dataset bias and small sample sizes."
            ),
            "conclusion": (
                "We recommend future work focus on graph models."
            ),
        },
    )
    # The structure phase writes a dict into ctx.outline; DraftContext
    # accepts the assignment post-construction. Set it explicitly to
    # mimic the real pipeline state.
    ctx.outline = {
        "paper_type": "Literature Review",
        "sections": [
            {"number": "1", "title": "Introduction", "target_words": 900},
            {"number": "2", "title": "Literature Review", "target_words": 1500},
            {"number": "3", "title": "Methodology", "target_words": 900},
            {"number": "4", "title": "Results", "target_words": 1200},
            {"number": "5", "title": "Discussion", "target_words": 900},
            {"number": "6", "title": "Conclusion", "target_words": 600},
        ],
    }
    return ctx


# ---------------------------------------------------------------------------
# Citation / FactCheck helper unit tests
# ---------------------------------------------------------------------------


def test_extract_citation_ids_basic():
    text = "Cited by [@abc] and also [@xyz] but not [@abc] again."
    assert _extract_citation_ids(text) == ["abc", "xyz"]


def test_extract_citation_ids_empty():
    assert _extract_citation_ids("") == []
    assert _extract_citation_ids("no citations here") == []


def test_extract_citation_ids_handles_whitespace():
    text = "[@paper:id] and [@ paper:2] should be returned."
    ids = _extract_citation_ids(text)
    assert "paper:id" in ids


def test_known_paper_ids_unions_three_sources():
    ctx = DraftContext(
        project_id="p",
        topic="t",
        reference_ids=["a", "b"],
        graph_node_ids=["b", "c"],
        paper_summaries=[
            {"paper_id": "c", "title": "T"},
            {"id": "d", "title": "D"},  # 'id' fallback
        ],
    )
    known = _known_paper_ids(ctx)
    assert known == {"a", "b", "c", "d"}


def test_deterministic_factcheck_finds_orphan():
    drafts = {
        "intro": "[@a] and [@z] are cited.",
        "method": "[@b] is fine, [@zzz] is not.",
    }
    fc = _deterministic_factcheck(drafts, known={"a", "b"})
    assert set(fc.verified) == {"a", "b"}
    assert set(fc.orphan) == {"z", "zzz"}
    assert fc.summary == "2 verified, 2 orphan"
    assert fc.passed is False


def test_deterministic_factcheck_passed_when_no_orphan():
    drafts = {"intro": "[@a] [@b] are both known."}
    fc = _deterministic_factcheck(drafts, known={"a", "b", "c"})
    assert fc.orphan == []
    assert fc.passed is True


def test_all_section_text_sorted():
    drafts = {
        "zebra": "Z body",
        "alpha": "A body",
        "middle": "M body",
    }
    text = _all_section_text(drafts)
    assert text.index("alpha") < text.index("middle") < text.index("zebra")


# ---------------------------------------------------------------------------
# Referee parser unit tests
# ---------------------------------------------------------------------------


def test_parse_referee_markdown_extracts_findings():
    md = (
        "# QA Report — T\n\n"
        "## 1. Overall assessment\nMinor revisions needed.\n\n"
        "## 2. Narrative consistency\n"
        "- Section 1 contradicts Section 3.\n"
        "* Section 2 has an unresolved thread.\n"
        "\n"
        "## 3. Voice and tone\n"
        "- Voice shifts to first person in the Conclusion.\n"
        "\n"
        "## 4. Argument flow\n"
        "- No issues found\n"
        "\n"
        "## 5. Citation usage\n"
        "- Three claims in the Results section lack citations.\n"
        "\n"
        "## 7. Strengths\n"
        "- Strong methods section.\n"
    )
    findings, verdict = _parse_referee_markdown(md)
    assert verdict == "minor revisions"
    # Findings captured (4 bullet sections, each with 1-2 bullets)
    assert len(findings) >= 6
    categories = {f.category for f in findings}
    assert "narrative" in categories
    assert "voice" in categories
    assert "citation" in categories
    # The 'Argument flow' category should have at least one finding
    flow_findings = [f for f in findings if f.category == "flow"]
    assert flow_findings and "No issues" in flow_findings[0].detail


def test_parse_referee_markdown_detects_publishable_verdict():
    md = (
        "## 1. Overall assessment\nThe draft is publishable as-is.\n"
        "## 2. Narrative consistency\n- No issues found\n"
    )
    _, verdict = _parse_referee_markdown(md)
    assert verdict == "publishable"


def test_parse_referee_markdown_detects_major_revisions():
    md = (
        "## 1. Overall assessment\nThis requires major revisions: the "
        "argument is incoherent in places.\n"
        "## 2. Narrative consistency\n- A\n"
    )
    _, verdict = _parse_referee_markdown(md)
    assert verdict == "major revisions"


def test_parse_referee_markdown_empty_returns_default():
    findings, verdict = _parse_referee_markdown("")
    assert findings == []
    assert verdict == "minor revisions"


def test_extract_section_name_handles_english_prefix():
    assert _extract_section_name("Section 3 contradicts Section 1.") == "3 contradicts Section 1"
    assert _extract_section_name("Voice shifts in the Conclusion.") == ""


def test_extract_section_name_handles_chinese_prefix():
    assert _extract_section_name("第3节: 论点跳跃") == "第3节"


def test_format_factcheck_block_includes_summary():
    fc = FactCheckResult(
        verified=["a", "b"],
        orphan=["z"],
        summary="2 verified, 1 orphan",
    )
    block = _format_factcheck_block(fc, lang="en")
    assert "## FactCheck" in block
    assert "2 verified, 1 orphan" in block
    assert "`[@a]`" in block
    assert "`[@z]`" in block
    assert "blocking" in block.lower()


def test_format_factcheck_block_handles_empty():
    fc = FactCheckResult()
    block = _format_factcheck_block(fc, lang="en")
    assert "No citations" in block


def test_format_factcheck_block_chinese():
    fc = FactCheckResult(verified=["a"], orphan=["z"], summary="1 verified, 1 orphan")
    block = _format_factcheck_block(fc, lang="zh")
    assert "引用核查" in block
    assert "已核验" in block
    assert "孤儿引用" in block


# ---------------------------------------------------------------------------
# Referee agent tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_referee_proces_qa_report():
    md = (
        "# QA Report — T\n\n"
        "## 1. Overall assessment\nPublishable after minor edits.\n\n"
        "## 2. Narrative consistency\n"
        "- Section 1 contradicts Section 3.\n\n"
        "## 7. Strengths\n- Clean structure.\n"
    )
    client = _MockLLMClient(md)
    ctx = _make_ctx_with_sections()
    result = await referee(ctx, client)
    assert isinstance(result, RefereeResult)
    # The LLM call's .strip()ed content is stored verbatim; tolerate
    # any trailing-newline difference.
    assert result.qa_markdown.strip() == md.strip()
    assert result.verdict == "publishable"
    assert result.findings  # at least one parsed finding
    narrative = [f for f in result.findings if f.category == "narrative"]
    assert narrative and "Section 1" in narrative[0].detail


@pytest.mark.asyncio
async def test_referee_records_raw_output():
    md = "## 1. Overall assessment\nMinor revisions.\n## 2. Narrative\n- ok"
    client = _MockLLMClient(md)
    ctx = _make_ctx_with_sections()
    result = await referee(ctx, client)
    assert result.raw_llm_output == md


# ---------------------------------------------------------------------------
# FactCheck agent tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_factcheck_detects_orphan_citation():
    ctx = _make_ctx_with_sections()
    fc = await factcheck(ctx, llm_client=None, use_llm=False)
    assert isinstance(fc, FactCheckResult)
    # "not_in_corpus" is cited in 'results' but not in any reference list
    assert "not_in_corpus" in fc.orphan
    # The known IDs should all be verified
    for pid in ("openalex:W100", "arxiv:2401.00001", "s2:hashA", "ref1", "ref2"):
        assert pid in fc.verified


@pytest.mark.asyncio
async def test_factcheck_returns_verified_list():
    ctx = _make_ctx_with_sections()
    fc = await factcheck(ctx, llm_client=None, use_llm=False)
    assert isinstance(fc.verified, list)
    assert "openalex:W100" in fc.verified
    # All verified IDs must actually be in the project's known set
    known = _known_paper_ids(ctx)
    for pid in fc.verified:
        assert pid in known


@pytest.mark.asyncio
async def test_factcheck_empty_section_drafts():
    ctx = DraftContext(project_id="p", topic="t")
    fc = await factcheck(ctx, llm_client=None, use_llm=False)
    assert fc.verified == []
    assert fc.orphan == []
    assert fc.summary == "0 verified, 0 orphan"
    assert fc.passed is True


@pytest.mark.asyncio
async def test_factcheck_passes_when_all_citations_resolved():
    ctx = _make_ctx_with_sections()
    # Replace the orphan citation in 'results' with a known one
    ctx.section_drafts["results"] = ctx.section_drafts["results"].replace(
        "[@not_in_corpus]", "[@ref1]"
    )
    fc = await factcheck(ctx, llm_client=None, use_llm=False)
    assert fc.orphan == []
    assert fc.passed is True


@pytest.mark.asyncio
async def test_factcheck_with_llm_appends_unsupported_claims():
    """When an LLM client is provided, FactCheck uses it to surface
    unsupported claims. The verified/orphan lists remain grounded in
    the deterministic pass."""
    llm_response = json.dumps(
        {
            "verified": ["openalex:W100"],   # ignored — deterministic wins
            "orphan": ["nope"],              # ignored — deterministic wins
            "unsupported_claims": [
                {
                    "section": "Discussion",
                    "sentence": "We found 42% improvement.",
                    "issue": "no_citation",
                }
            ],
            "summary": "irrelevant",
        }
    )
    client = _MockLLMClient(llm_response)
    ctx = _make_ctx_with_sections()
    fc = await factcheck(ctx, llm_client=client, use_llm=True)
    # Verified/orphan stay deterministic
    assert "not_in_corpus" in fc.orphan
    # Unsupported claims come from the LLM
    assert len(fc.unsupported_claims) == 1
    assert fc.unsupported_claims[0].section == "Discussion"


@pytest.mark.asyncio
async def test_factcheck_llm_garbage_returns_empty_claims():
    client = _MockLLMClient("totally not json")
    ctx = _make_ctx_with_sections()
    fc = await factcheck(ctx, llm_client=client, use_llm=True)
    # Deterministic pass still succeeded
    assert "not_in_corpus" in fc.orphan
    # LLM claim detection failed gracefully
    assert fc.unsupported_claims == []


# ---------------------------------------------------------------------------
# Compile phase helper tests
# ---------------------------------------------------------------------------


def test_ordered_section_names_imrad_order():
    drafts = {
        "conclusion": "C",
        "introduction": "I",
        "methodology": "M",
        "results": "R",
        "literature_review": "L",
        "discussion": "D",
    }
    ordered = _ordered_section_names(drafts)
    assert ordered == [
        "introduction",
        "literature_review",
        "methodology",
        "results",
        "discussion",
        "conclusion",
    ]


def test_ordered_section_names_unknown_go_to_end():
    drafts = {
        "zz_unknown": "Z",
        "introduction": "I",
        "aa_unknown": "A",
    }
    ordered = _ordered_section_names(drafts)
    assert ordered[0] == "introduction"
    assert set(ordered[1:]) == {"zz_unknown", "aa_unknown"}


def test_referenced_paper_ids_in_order_unique():
    ctx = _make_ctx_with_sections()
    ids = _referenced_paper_ids(ctx)
    # The "results" section is processed after "introduction" etc.,
    # but the order should match first-occurrence in sorted section
    # order (introduction, literature_review, ..., results, ...).
    # The unique IDs in first-occurrence order from sorted sections:
    expected_first_seen = [
        "openalex:W100",   # introduction
        "ref1",            # introduction
        "arxiv:2401.00001",  # literature_review
        "s2:hashA",        # literature_review
        "ref2",            # methodology
        "not_in_corpus",   # results (orphan)
    ]
    for pid in expected_first_seen:
        assert pid in ids


def test_format_authors_handles_known_shapes():
    assert _format_authors([]) == "Unknown author"
    assert _format_authors(["Alice Smith"]).startswith("Smith")
    assert _format_authors(["Alice Smith", "Bob Jones"]).startswith("Smith, A.")
    # 7 authors truncates to 6 + "et al."
    out = _format_authors([f"Author {i}" for i in range(7)])
    assert "et al." in out


def test_format_reference_line_apa():
    line = _format_reference_line(
        "x",
        {"authors": ["Alice Smith", "Bob Jones"], "year": 2023,
         "title": "T", "venue": "V", "doi": "10.1/x"},
        CitationStyle.APA,
    )
    assert line.startswith("[@x]")
    assert "(2023)." in line
    assert "T." in line
    assert "V." in line
    assert "10.1/x" in line


def test_format_reference_line_ieee():
    line = _format_reference_line(
        "x",
        {"authors": ["Alice Smith"], "year": 2023, "title": "T", "venue": "V"},
        CitationStyle.IEEE,
    )
    assert line.startswith("[@x]")
    assert '"T,"' in line
    assert ", V" in line
    assert ", 2023" in line


def test_format_reference_line_handles_missing_metadata():
    line = _format_reference_line(
        "x", {"authors": [], "year": None, "title": "", "venue": ""},
        CitationStyle.APA,
    )
    assert "[@x]" in line
    assert "n.d." in line


def test_format_qa_header_extracts_first_paragraph():
    report = (
        "# QA Report\n"
        "## 1. Overall assessment\nThis is the verdict line we want.\n"
        "## 2. Narrative\n- A finding\n"
    )
    header = _format_qa_header(report, lang="en")
    assert "QA verdict" in header
    assert "verdict line" in header


def test_format_qa_header_chinese_label():
    report = "## 1. 总体评价\n这是我们想要的判定。\n"
    header = _format_qa_header(report, lang="zh")
    assert "QA 判定" in header
    assert "判定" in header


def test_format_qa_header_empty_returns_empty():
    assert _format_qa_header("", "en") == ""
    assert _format_qa_header(None, "en") == ""


def test_collect_paper_metadata_merges_sources():
    ctx = _make_ctx_with_sections()
    papers = _collect_paper_metadata(ctx)
    by_id = {p["id"]: p for p in papers}
    assert "openalex:W100" in by_id
    assert "arxiv:2401.00001" in by_id
    assert "s2:hashA" in by_id
    assert by_id["openalex:W100"]["title"] == "Deep learning for molecular property prediction"


def test_assemble_body_uses_outline_numbers():
    ctx = _make_ctx_with_sections()
    body = _assemble_body(ctx)
    # Outline section numbers should appear
    assert "## 1 Introduction" in body
    assert "## 2 Literature Review" in body
    assert "## 6 Conclusion" in body
    # Body content from each section should appear
    assert "Drug discovery is expensive" in body
    assert "We followed PRISMA" in body


def test_assemble_body_without_outline_uses_section_names():
    ctx = _make_ctx_with_sections()
    ctx.outline = None
    body = _assemble_body(ctx)
    assert "## Introduction" in body
    assert "Drug discovery is expensive" in body


def test_compose_final_includes_all_pieces():
    final = _compose_final(
        title="My Paper",
        abstract="This is the abstract.",
        body="## 1 Introduction\n\nHello.",
        references="## References\n\n- [@x]",
        qa_header="> **QA verdict:** ok\n",
        qa_summary="ok",
    )
    assert final.startswith("> **QA verdict:** ok")
    assert "# My Paper" in final
    assert "## Abstract" in final
    assert "This is the abstract." in final
    assert "## 1 Introduction" in final
    assert "## References" in final
    assert "**QA summary:** ok" in final


def test_derive_qa_summary_reads_quality_history():
    ctx = DraftContext(project_id="p", topic="t")
    ctx.quality_history.append(
        {
            "phase": PhaseName.VALIDATE.value,
            "verdict": "publishable",
            "verified_count": 5,
            "orphan_count": 0,
        }
    )
    summary = _derive_qa_summary(ctx)
    assert "publishable" in summary
    assert "5 verified" in summary
    assert "0 orphan" in summary


def test_derive_qa_summary_returns_empty_for_wrong_phase():
    ctx = DraftContext(project_id="p", topic="t")
    ctx.quality_history.append({"phase": "compose", "verdict": "ok"})
    assert _derive_qa_summary(ctx) == ""


def test_render_references_markdown_handles_empty():
    assert _render_references_markdown([], {}, CitationStyle.APA) == ""
    out = _render_references_markdown(
        ["a"], {"a": {"authors": ["X"], "year": 2024, "title": "T", "venue": "V"}},
        CitationStyle.APA,
    )
    assert "## References" in out
    assert "[@a]" in out


def test_heuristic_abstract_truncates_to_target():
    ctx = _make_ctx_with_sections()
    body = _assemble_body(ctx)
    abstract = _heuristic_abstract(ctx, body, target_words=10)
    words = abstract.split()
    assert len(words) <= 10
    assert abstract.endswith(".")


def test_heuristic_abstract_empty_sections():
    ctx = DraftContext(project_id="p", topic="t", section_drafts={})
    assert _heuristic_abstract(ctx, "", target_words=20) == ""


# ---------------------------------------------------------------------------
# Compiler agent tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compiler_assembles_final_draft():
    ctx = _make_ctx_with_sections()
    result = await compiler(ctx, llm_client=None)
    assert isinstance(result, CompilerResult)
    assert result.final_draft
    assert "# " in result.final_draft  # has a title heading
    assert "## Abstract" in result.final_draft
    # References for every cited paper should be rendered
    for pid in ("openalex:W100", "arxiv:2401.00001", "s2:hashA", "ref1", "ref2"):
        assert f"[@{pid}]" in result.final_draft
    # Sections appear in IMRaD order
    assert result.final_draft.index("## 1 Introduction") < result.final_draft.index("## 6 Conclusion")


@pytest.mark.asyncio
async def test_compiler_includes_qa_report_header():
    ctx = _make_ctx_with_sections()
    ctx.qa_report = "## 1. Overall assessment\nThis is a test verdict line.\n"
    result = await compiler(ctx, llm_client=None)
    assert "QA verdict" in result.final_draft
    assert "test verdict line" in result.final_draft


@pytest.mark.asyncio
async def test_compiler_with_no_sections_returns_empty():
    ctx = DraftContext(project_id="p", topic="t", section_drafts={})
    result = await compiler(ctx, llm_client=None)
    assert result.final_draft == ""
    assert result.title == ""


@pytest.mark.asyncio
async def test_compiler_with_llm_uses_suggested_title():
    payload = json.dumps(
        {
            "title": "ML for Drug Discovery: A Review",
            "abstract": "We survey ML methods for drug discovery.",
            "body_markdown": "",
            "references_markdown": "",
            "qa_summary": "ok",
        }
    )
    client = _MockLLMClient(payload)
    ctx = _make_ctx_with_sections()
    result = await compiler(ctx, llm_client=client)
    assert "ML for Drug Discovery" in result.title
    assert "survey" in result.abstract.lower()


@pytest.mark.asyncio
async def test_compiler_handles_llm_garbage():
    client = _MockLLMClient("this is not json at all")
    ctx = _make_ctx_with_sections()
    result = await compiler(ctx, llm_client=client)
    # Falls back to topic as title; final draft is still assembled
    assert result.title == ctx.topic
    assert result.final_draft
    # The introduction section should be present, regardless of whether
    # the outline supplied a number for it.
    assert "Introduction" in result.final_draft


@pytest.mark.asyncio
async def test_compiler_renders_references_in_apa_style():
    ctx = _make_ctx_with_sections()
    ctx.citation_style = CitationStyle.APA
    result = await compiler(ctx, llm_client=None)
    # APA reference should have year in parens
    assert "(2023)." in result.references_markdown or "(2024)." in result.references_markdown


@pytest.mark.asyncio
async def test_compiler_renders_references_in_ieee_style():
    ctx = _make_ctx_with_sections()
    ctx.citation_style = CitationStyle.IEEE
    result = await compiler(ctx, llm_client=None)
    # IEEE reference should have the title in quotes
    assert '"' in result.references_markdown
    assert ", 2023" in result.references_markdown or ", 2024" in result.references_markdown


@pytest.mark.asyncio
async def test_compiler_renders_references_in_mla_style():
    ctx = _make_ctx_with_sections()
    ctx.citation_style = CitationStyle.MLA
    result = await compiler(ctx, llm_client=None)
    assert "## References" in result.references_markdown
    # MLA-style title in quotes
    assert '"' in result.references_markdown


# ---------------------------------------------------------------------------
# abstract_writer tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abstract_writer_with_llm():
    payload = json.dumps({"abstract": "We present a review of drug discovery AI."})
    client = _MockLLMClient(payload)
    ctx = _make_ctx_with_sections()
    abstract = await abstract_writer(ctx, client)
    assert "drug discovery" in abstract.lower()


@pytest.mark.asyncio
async def test_abstract_writer_falls_back_on_llm_failure():
    class _BoomClient:
        class chat:
            class completions:
                async def create(*a, **k):
                    raise RuntimeError("network down")

    ctx = _make_ctx_with_sections()
    abstract = await abstract_writer(ctx, _BoomClient())
    # Heuristic abstract: first non-heading paragraph of first section
    assert abstract
    assert abstract.endswith(".")


@pytest.mark.asyncio
async def test_abstract_writer_empty_sections_returns_empty():
    ctx = DraftContext(project_id="p", topic="t", section_drafts={})
    client = _MockLLMClient([])
    abstract = await abstract_writer(ctx, client)
    assert abstract == ""


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_validate_phase_marks_done_with_llm():
    referee_md = (
        "## 1. Overall assessment\nPublishable.\n"
        "## 2. Narrative consistency\n- No issues found\n"
    )
    factcheck_payload = json.dumps({"unsupported_claims": []})
    client = _MockLLMClient([referee_md, factcheck_payload])
    ctx = _make_ctx_with_sections()
    result = await run_validate_phase(ctx, llm_client=client)
    assert result.is_phase_done(PhaseName.VALIDATE)
    assert ctx.qa_report
    assert "## FactCheck" in ctx.qa_report
    assert "not_in_corpus" in ctx.qa_report
    # The validate-phase verdict was appended to quality_history
    assert any(
        isinstance(h, dict) and h.get("phase") == PhaseName.VALIDATE.value
        for h in ctx.quality_history
    )


@pytest.mark.asyncio
async def test_run_validate_phase_without_llm_still_succeeds():
    ctx = _make_ctx_with_sections()
    result = await run_validate_phase(ctx, llm_client=None)
    assert result.is_phase_done(PhaseName.VALIDATE)
    # Referee section should mark itself as skipped
    assert "skipped" in ctx.qa_report.lower()
    # FactCheck still runs in deterministic mode → orphan citations flagged
    assert "not_in_corpus" in ctx.qa_report


@pytest.mark.asyncio
async def test_run_validate_phase_records_failure(monkeypatch):
    """If a sub-phase raises, the VALIDATE phase is marked FAILED and
    the exception is re-raised."""

    async def _boom(*a, **k):
        raise RuntimeError("synthetic validate failure")

    # Monkeypatch factcheck to fail
    monkeypatch.setattr(validate, "factcheck", _boom)
    ctx = _make_ctx_with_sections()
    with pytest.raises(RuntimeError, match="synthetic validate failure"):
        await run_validate_phase(ctx, llm_client=None)

    assert ctx.phase_results[PhaseName.VALIDATE].status is PhaseStatus.FAILED
    assert "synthetic" in ctx.phase_results[PhaseName.VALIDATE].error


@pytest.mark.asyncio
async def test_run_validate_phase_referee_failure_does_not_block(monkeypatch):
    """If only the Referee step fails, the phase still succeeds and
    FactCheck output is preserved."""
    from app.services.draft_pipeline.phases import validate as validate_module

    async def _boom_referee(*a, **k):
        raise RuntimeError("referee down")

    monkeypatch.setattr(validate_module, "referee", _boom_referee)
    ctx = _make_ctx_with_sections()
    result = await run_validate_phase(ctx, llm_client=_MockLLMClient([]))
    assert result.is_phase_done(PhaseName.VALIDATE)
    assert "referee down" in ctx.qa_report.lower()
    # But FactCheck still produced its block
    assert "## FactCheck" in ctx.qa_report


@pytest.mark.asyncio
async def test_run_compile_phase_marks_done_and_attaches_score():
    ctx = _make_ctx_with_sections()
    result = await run_compile_phase(ctx, llm_client=None)
    assert result.is_phase_done(PhaseName.COMPILE)
    assert ctx.final_draft
    # Quality score appended to history
    quality_scores = [
        h for h in ctx.quality_history if hasattr(h, "total")
    ]
    assert quality_scores
    # The score is a QualityScore dataclass with 5 dimensions
    score = quality_scores[-1]
    assert 0 <= score.total <= 125
    assert hasattr(score, "word_count")
    assert hasattr(score, "citation_density")


@pytest.mark.asyncio
async def test_run_compile_phase_records_failure(monkeypatch):
    """If the Compiler raises, COMPILE is marked FAILED and the
    exception is re-raised."""

    async def _boom(*a, **k):
        raise RuntimeError("synthetic compile failure")

    monkeypatch.setattr(compile, "compiler", _boom)
    ctx = _make_ctx_with_sections()
    with pytest.raises(RuntimeError, match="synthetic compile failure"):
        await run_compile_phase(ctx, llm_client=None)

    assert ctx.phase_results[PhaseName.COMPILE].status is PhaseStatus.FAILED
    assert "synthetic" in ctx.phase_results[PhaseName.COMPILE].error


@pytest.mark.asyncio
async def test_run_compile_phase_uses_custom_quality_gate():
    from app.services.draft_pipeline.quality_gate import QualityDecision, QualityGate

    class _PermissiveGate(QualityGate):
        PASS_THRESHOLD = 1  # anything is a pass

    ctx = _make_ctx_with_sections()
    result = await run_compile_phase(ctx, llm_client=None, quality_gate=_PermissiveGate())
    assert result.is_phase_done(PhaseName.COMPILE)
    scores = [h for h in ctx.quality_history if hasattr(h, "decision")]
    assert scores
    assert scores[-1].decision is QualityDecision.PASS
