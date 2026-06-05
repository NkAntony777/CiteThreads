"""
Refine phase — three sequential LLM-driven refinement passes on the
section drafts produced by the Compose phase.

This module is a CTDP extension inspired by opendraft's compose phase,
which applies three post-draft refinement passes in order:

1. **Polish** — per-section language cleanup, repetition removal,
   passive→active conversion. One LLM call per section.
2. **Voice** — per-section voice / tone / register unification against
   a short excerpt from a sibling section. One LLM call per section.
3. **Entropy** — cross-section contradiction detection. **One** LLM
   call covering all six sections at once (this is the only pass that
   sees more than one section at a time).

All three passes are optional and degrade gracefully:

* Without an LLM client, each pass is a no-op and the input drafts
  are returned unchanged.
* If the LLM returns something that cannot be parsed, the input
  drafts are returned unchanged for that pass and a warning is
  logged. The pipeline never blocks on a malformed refinement.

Per-section passes (polish, voice) loop sequentially over the section
drafts; the entropy pass sends them all in one request. This means
entropy is materially cheaper per section but more sensitive to
context length.

The orchestrator :func:`run_refine_phase` runs the requested passes
in order, mutates ``ctx.section_drafts`` in place, and records which
passes were applied in ``ctx.phase_results[PhaseName.COMPOSE].passes``
so the UI / runner can surface what happened.

The passes are kept *inside* the COMPOSE phase bucket rather than
becoming a new ``PhaseName.REFINE`` value. Trade-off:

* **Keep inside COMPOSE**: refinement IS part of composition; the
  end-user sees a single "compose" step in the progress bar; the
  5-bucket dispatchable set stays at 5 (so the progress bar reaches
  100% naturally).
* **New enum value**: a UI could render "polishing" / "voicing" /
  "entropying" as distinct progress events, but every consumer
  (``progress_pct``, runner, router, frontend progress UI) would
  need to know about a 6th bucket.
* **Decision**: stay inside COMPOSE. The 3 passes are recorded on
  ``PhaseResult.passes`` so the information is available without
  restructuring the dispatchable phase set.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from openai import AsyncOpenAI

from ..context import DraftContext, PhaseName, PhaseStatus
from ..prompts import load_prompt
from .compose import SECTION_NAMES, _strip_metadata_sections
from .research import _resolve_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


# The 6 keys that an entropy-pass response must include. They are
# slightly different from the SECTION_NAMES (Crafter) convention:
# ``SECTION_NAMES`` uses snake_case (``"literature_review"``), but the
# entropy prompt's output schema uses the canonical IMRaD names
# (``"literature_review"``, ``"methodology"``). They are the same
# strings — we re-state them here so the entropy pass has a
# declarative contract that's easy to unit-test.
ENTROPY_SECTION_KEYS: List[str] = list(SECTION_NAMES)

# Pass identifiers used in ``ctx.phase_results[COMPOSE].passes``.
PASS_POLISH = "polish"
PASS_VOICE = "voice"
PASS_ENTROPY = "entropy"
ALL_PASSES: List[str] = [PASS_POLISH, PASS_VOICE, PASS_ENTROPY]


@dataclass
class RefinePassResult:
    """Output of a single refinement pass.

    ``drafts`` is the new ``dict[str, str]`` of section bodies.
    ``changed`` lists the section names whose body actually changed
    (string compare against the input).
    ``issues`` is reserved for structured findings (used by entropy
    to record cross-section contradictions); empty for polish / voice.
    """

    drafts: Dict[str, str] = field(default_factory=dict)
    changed: List[str] = field(default_factory=list)
    issues: List[dict] = field(default_factory=list)
    used_llm: bool = False
    raw_llm_output: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _language_instruction(language: str) -> str:
    """Same wording as compose.py so the LLM behaviour is consistent."""
    if (language or "").lower().startswith("zh"):
        return "\n\n请用中文撰写整个章节（包括所有标题和段落）。"
    return "\n\nPlease write the entire section in English."


def _format_citation_list(ctx: DraftContext) -> str:
    """Available citation IDs. Mirrors compose.py's helper but kept
    local to avoid a circular-import risk and to keep refine.py
    self-contained for the tests."""
    ids = list(ctx.reference_ids or [])
    if not ids:
        for s in ctx.paper_summaries or []:
            if isinstance(s, dict) and s.get("paper_id"):
                ids.append(s["paper_id"])
    if not ids:
        return "(no citation database available — use placeholders)"
    return "\n".join(f"- [@{pid}]" for pid in ids)


def _excerpt(text: str, max_chars: int) -> str:
    """Pull a short excerpt from a sibling section so the voice pass
    has a concrete style reference. Truncates with an ellipsis."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _strip_code_fences(text: str) -> str:
    """Tolerate ```json ... ``` fences and stray prose around the body."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    return text.strip()


def _extract_first_json_object(text: str) -> Optional[dict]:
    """Find the first balanced ``{...}`` JSON object in ``text`` and
    parse it. Returns ``None`` on failure."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                snippet = text[start : i + 1]
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError:
                    return None
    return None


def _extract_first_json_array(text: str) -> Optional[list]:
    """Find the first balanced ``[...]`` JSON array in ``text``."""
    start = text.find("[")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                snippet = text[start : i + 1]
                try:
                    data = json.loads(snippet)
                except json.JSONDecodeError:
                    return None
                if isinstance(data, list):
                    return data
                return None
    return None


def _diff_changed(original: Dict[str, str], updated: Dict[str, str]) -> List[str]:
    """Return the keys whose value differs between two section dicts."""
    out: List[str] = []
    keys = set(original) | set(updated)
    for k in keys:
        if (original.get(k) or "") != (updated.get(k) or ""):
            out.append(k)
    return sorted(out)


def _no_op_result(drafts: Dict[str, str], used_llm: bool = False) -> RefinePassResult:
    """Build a RefinePassResult that reports no changes."""
    return RefinePassResult(
        drafts=dict(drafts),
        changed=[],
        issues=[],
        used_llm=used_llm,
        raw_llm_output="",
    )


# ---------------------------------------------------------------------------
# Polish — per-section language cleanup
# ---------------------------------------------------------------------------


async def polish(
    ctx: DraftContext,
    drafts: Dict[str, str],
    llm_client: Optional[AsyncOpenAI] = None,
) -> RefinePassResult:
    """Polish each section independently.

    Behaviour
    ---------
    * ``llm_client is None``  → return input drafts unchanged.
    * Empty input              → return ``{}``.
    * LLM returns garbage       → log a warning, return input drafts
      for that section unchanged. Other sections may still succeed.
    * LLM returns valid JSON    → use the ``polished`` field as the
      new section body.

    The function is sequential because section count is small (≤ 6)
    and the Compose phase's LLM client is configured for a single
    request stream — there is no benefit to parallel calls here, and
    parallel calls can blow past the rate limit.
    """
    if not drafts:
        return RefinePassResult(drafts={}, changed=[], used_llm=False)
    if llm_client is None:
        logger.info("polish: no LLM client, returning input unchanged")
        return _no_op_result(drafts, used_llm=False)

    prompt_body = load_prompt("polish", lang=ctx.language)
    cite_list = _format_citation_list(ctx)
    updated: Dict[str, str] = dict(drafts)
    used = False
    last_raw = ""

    for section_name, body in drafts.items():
        user_msg = (
            f"Topic: {ctx.topic}\n\n"
            f"Section: {section_name}\n\n"
            f"Draft to polish:\n```\n{body}\n```\n\n"
            f"Allowed paper IDs (use [@paper_id] only from this list):\n"
            f"{cite_list}{_language_instruction(ctx.language)}"
        )
        # LLM call errors propagate to the orchestrator so the phase
        # is marked FAILED (consistent with compose.py's refiner). A
        # parse failure (JSON object missing the "polished" key) is
        # best-effort and degrades to keeping the input for that
        # section.
        response = await llm_client.chat.completions.create(
            model=_resolve_model(llm_client),
            messages=[
                {"role": "system", "content": prompt_body},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=4000,
        )
        used = True
        raw = (response.choices[0].message.content or "").strip()
        last_raw = raw
        cleaned = _strip_code_fences(raw)
        parsed = _extract_first_json_object(cleaned)
        polished_body: Optional[str] = None
        if isinstance(parsed, dict):
            candidate = parsed.get("polished") or parsed.get("body")
            if isinstance(candidate, str) and candidate.strip():
                polished_body = _strip_metadata_sections(candidate)
        if not polished_body:
            logger.warning(
                "polish: unparsable response for section %r, keeping input",
                section_name,
            )
            continue
        updated[section_name] = polished_body

    return RefinePassResult(
        drafts=updated,
        changed=_diff_changed(drafts, updated),
        issues=[],
        used_llm=used,
        raw_llm_output=last_raw,
    )


# ---------------------------------------------------------------------------
# Voice — per-section tone / register unification
# ---------------------------------------------------------------------------


async def voice(
    ctx: DraftContext,
    drafts: Dict[str, str],
    llm_client: Optional[AsyncOpenAI] = None,
) -> RefinePassResult:
    """Unify each section's voice against a short sibling excerpt.

    The reference excerpt is the *next* section in canonical IMRaD
    order, or the previous one if the current section is last. The
    reference is truncated to keep the prompt small.

    Without an LLM client, this is a no-op.
    """
    if not drafts:
        return RefinePassResult(drafts={}, changed=[], used_llm=False)
    if llm_client is None:
        logger.info("voice: no LLM client, returning input unchanged")
        return _no_op_result(drafts, used_llm=False)

    prompt_body = load_prompt("voice", lang=ctx.language)
    cite_list = _format_citation_list(ctx)
    updated: Dict[str, str] = dict(drafts)
    used = False
    last_raw = ""

    # Canonical order so the reference is always a real sibling.
    ordered = [s for s in SECTION_NAMES if s in drafts]
    # Anything not in SECTION_NAMES (custom sections) gets appended so
    # we still process it, with a reference drawn from the first
    # SECTION_NAMES hit.
    for s in drafts:
        if s not in ordered:
            ordered.append(s)

    for idx, section_name in enumerate(ordered):
        # Reference: the *next* section in canonical order, with wrap.
        ref_name = ordered[(idx + 1) % len(ordered)]
        ref_excerpt = _excerpt(drafts.get(ref_name, ""), 1500)
        body = drafts[section_name]
        user_msg = (
            f"Topic: {ctx.topic}\n\n"
            f"Section to align: {section_name}\n\n"
            f"Voice reference (excerpt from a sibling section, {ref_name}):\n"
            f"```\n{ref_excerpt}\n```\n\n"
            f"Section to rewrite:\n```\n{body}\n```\n\n"
            f"Allowed paper IDs (use [@paper_id] only from this list):\n"
            f"{cite_list}{_language_instruction(ctx.language)}"
        )
        # LLM call errors propagate; only parse failures degrade.
        response = await llm_client.chat.completions.create(
            model=_resolve_model(llm_client),
            messages=[
                {"role": "system", "content": prompt_body},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.25,
            max_tokens=4000,
        )
        used = True
        raw = (response.choices[0].message.content or "").strip()
        last_raw = raw
        cleaned = _strip_code_fences(raw)
        parsed = _extract_first_json_object(cleaned)
        voiced_body: Optional[str] = None
        if isinstance(parsed, dict):
            candidate = parsed.get("voiced") or parsed.get("body")
            if isinstance(candidate, str) and candidate.strip():
                voiced_body = _strip_metadata_sections(candidate)
        if not voiced_body:
            logger.warning(
                "voice: unparsable response for section %r, keeping input",
                section_name,
            )
            continue
        updated[section_name] = voiced_body

    return RefinePassResult(
        drafts=updated,
        changed=_diff_changed(drafts, updated),
        issues=[],
        used_llm=used,
        raw_llm_output=last_raw,
    )


# ---------------------------------------------------------------------------
# Entropy — cross-section contradiction detection
# ---------------------------------------------------------------------------


def _build_entropy_drafts_blob(drafts: Dict[str, str]) -> str:
    """Render the section drafts as a single text block, one section
    per heading, so the LLM can see them in canonical order."""
    parts: List[str] = []
    ordered = [s for s in SECTION_NAMES if s in drafts]
    for s in drafts:
        if s not in ordered:
            ordered.append(s)
    for s in ordered:
        body = drafts.get(s) or ""
        parts.append(f"\n\n### SECTION: {s}\n\n{body}")
    return "".join(parts).strip() + "\n"


def _parse_entropy_response(
    raw: str, expected_keys: Iterable[str]
) -> Optional[Dict[str, Any]]:
    """Parse the entropy response. Returns a dict with two keys:
    ``sections`` (dict[str, str]) and ``issues_found`` (list[dict]),
    or ``None`` if the response is unusable.
    """
    cleaned = _strip_code_fences(raw)
    parsed = _extract_first_json_object(cleaned)
    if not isinstance(parsed, dict):
        return None
    sections_obj = parsed.get("sections")
    issues_obj = parsed.get("issues_found")
    if not isinstance(sections_obj, dict):
        return None
    # Per-key normalisation: only accept string values; ignore the
    # rest. Missing keys fall through to the caller (which will keep
    # the original input for that key).
    normalised: Dict[str, str] = {}
    for key in expected_keys:
        v = sections_obj.get(key)
        if isinstance(v, str) and v.strip():
            normalised[key] = _strip_metadata_sections(v)
    if not isinstance(issues_obj, list):
        issues_obj = []
    return {"sections": normalised, "issues_found": issues_obj}


async def entropy(
    ctx: DraftContext,
    drafts: Dict[str, str],
    llm_client: Optional[AsyncOpenAI] = None,
) -> RefinePassResult:
    """Run the cross-section contradiction pass.

    Single LLM call covering all sections. Without an LLM, returns
    the input unchanged. If the LLM returns garbage, returns the
    input unchanged and logs a warning — the entropy pass is
    best-effort.
    """
    if not drafts:
        return RefinePassResult(drafts={}, changed=[], used_llm=False)
    if llm_client is None:
        logger.info("entropy: no LLM client, returning input unchanged")
        return _no_op_result(drafts, used_llm=False)

    prompt_body = load_prompt("entropy", lang=ctx.language)
    cite_list = _format_citation_list(ctx)
    blob = _build_entropy_drafts_blob(drafts)
    if len(blob) > 30_000:
        # Trim the largest section first, then trim from the end.
        # Entropy needs breadth, not depth.
        ordered = sorted(
            ((k, len(v or "")) for k, v in drafts.items()),
            key=lambda kv: kv[1],
            reverse=True,
        )
        for k, _ in ordered:
            if len(blob) <= 30_000:
                break
            drafts_trim = dict(drafts)
            drafts_trim[k] = _excerpt(drafts.get(k, ""), 2000)
            blob = _build_entropy_drafts_blob(drafts_trim)

    user_msg = (
        f"Topic: {ctx.topic}\n\n"
        f"Section drafts (all six IMRaD sections, in order):\n"
        f"```\n{blob}\n```\n\n"
        f"Allowed paper IDs (use [@paper_id] only from this list):\n"
        f"{cite_list}{_language_instruction(ctx.language)}"
    )
    # LLM call errors propagate; only parse failures degrade.
    response = await llm_client.chat.completions.create(
        model=_resolve_model(llm_client),
        messages=[
            {"role": "system", "content": prompt_body},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.15,
        max_tokens=6000,
    )
    raw = (response.choices[0].message.content or "").strip()
    parsed = _parse_entropy_response(raw, ENTROPY_SECTION_KEYS)
    if not parsed:
        logger.warning(
            "entropy: unparsable response, keeping input drafts. "
            "First 200 chars: %r",
            raw[:200],
        )
        return RefinePassResult(
            drafts=dict(drafts),
            changed=[],
            issues=[],
            used_llm=True,
            raw_llm_output=raw,
        )

    # Merge: for any key the LLM did not return, keep the input.
    merged: Dict[str, str] = dict(drafts)
    for k, v in parsed["sections"].items():
        if k in drafts:  # only update keys we sent
            merged[k] = v
    # Also accept custom keys the LLM happened to return (e.g. user
    # added a "preface" section) — only if the input had them.
    for k in drafts:
        if k in parsed["sections"] and k not in merged:
            merged[k] = parsed["sections"][k]

    return RefinePassResult(
        drafts=merged,
        changed=_diff_changed(drafts, merged),
        issues=parsed["issues_found"],
        used_llm=True,
        raw_llm_output=raw,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


# Map of pass name → coroutine factory. The orchestrator iterates
# this map in canonical order so callers passing a partial ``passes``
# list still get a deterministic execution sequence.
_PASS_RUNNERS = {
    PASS_POLISH: polish,
    PASS_VOICE: voice,
    PASS_ENTROPY: entropy,
}


def _coerce_passes_arg(passes: Optional[Iterable[str]]) -> List[str]:
    """Normalise the ``passes`` argument. Unknown names are dropped
    (and logged) rather than raising — callers passing stale values
    from a checkpoint should not crash the orchestrator."""
    if passes is None:
        return list(ALL_PASSES)
    seen: List[str] = []
    for name in passes:
        if name in _PASS_RUNNERS and name not in seen:
            seen.append(name)
        elif name not in _PASS_RUNNERS:
            logger.warning("run_refine_phase: ignoring unknown pass %r", name)
    if not seen:
        # If the caller passed only invalid names, fall back to all
        # three so the orchestrator still does *something* useful.
        return list(ALL_PASSES)
    return seen


async def run_refine_phase(
    ctx: DraftContext,
    llm_client: Optional[AsyncOpenAI] = None,
    *,
    passes: Optional[Iterable[str]] = None,
) -> DraftContext:
    """Run the requested refinement passes in sequence.

    Each pass reads ``ctx.section_drafts``, returns a new dict, and
    the orchestrator writes it back. This means a pass's output
    becomes the next pass's input — running polish → voice → entropy
    is the canonical sequence.

    Behaviour
    ---------
    * Marks ``PhaseName.COMPOSE`` RUNNING → SUCCEEDED / FAILED (this
      phase lives inside COMPOSE; see module docstring for rationale).
    * Records the applied pass names in
      ``ctx.phase_results[PhaseName.COMPOSE].passes`` so the UI can
      show "polish, voice, entropy applied" without parsing
      ``raw_llm_output``.
    * Without an LLM client, every pass is a no-op and the
      ``ctx.section_drafts`` dict is left unchanged. ``used_llm``
      on the per-pass results is False.
    * If a pass raises, the exception is logged, the phase is
      marked FAILED, and the exception is re-raised. The previous
      pass outputs are still in ``ctx.section_drafts`` so a retry
      has a clean input.

    Returns the same ``ctx`` for chaining.
    """
    ctx.mark_phase(PhaseName.COMPOSE, PhaseStatus.RUNNING)
    requested = _coerce_passes_arg(passes)
    # Per-pass results, kept on the PhaseResult for downstream
    # inspection (and for the unit tests).
    pass_summaries: List[dict] = []

    try:
        # Ensure we have a drafts dict to operate on. The Compose
        # orchestrator normally populates this, but tests may call
        # run_refine_phase on a bare ctx.
        if not isinstance(ctx.section_drafts, dict):
            ctx.section_drafts = {}

        for pass_name in requested:
            runner = _PASS_RUNNERS[pass_name]
            logger.info("refine: running pass %s", pass_name)
            result = await runner(ctx, ctx.section_drafts, llm_client)
            # Commit the new drafts back to ctx.
            ctx.section_drafts = dict(result.drafts)
            pass_summaries.append(
                {
                    "name": pass_name,
                    "used_llm": result.used_llm,
                    "changed": list(result.changed),
                    "issues": list(result.issues),
                }
            )
            if ctx.cancellation_requested:
                raise RuntimeError("Refine phase cancelled by request")

        # Record applied passes on the COMPOSE PhaseResult. We do
        # NOT clobber an earlier passes list — we *append* so
        # calling run_refine_phase twice (e.g. once with polish,
        # once with voice) produces a complete history.
        compose_result = ctx.phase_results.get(PhaseName.COMPOSE)
        if compose_result is None:
            # The Compose phase hasn't been marked yet (e.g. tests
            # calling refine before compose). Mark it SUCCEEDED with
            # the passes so the runner sees a clean state.
            ctx.mark_phase(PhaseName.COMPOSE, PhaseStatus.SUCCEEDED)
            compose_result = ctx.phase_results[PhaseName.COMPOSE]
        for s in pass_summaries:
            name = s["name"]
            if name not in compose_result.passes:
                compose_result.passes.append(name)

        logger.info(
            "refine: applied passes=%s (changed sections: %s)",
            [s["name"] for s in pass_summaries],
            [s["changed"] for s in pass_summaries],
        )
    except Exception as e:
        ctx.mark_phase(PhaseName.COMPOSE, PhaseStatus.FAILED, error=str(e))
        logger.exception("refine: phase failed")
        raise

    ctx.mark_phase(PhaseName.COMPOSE, PhaseStatus.SUCCEEDED)
    return ctx
