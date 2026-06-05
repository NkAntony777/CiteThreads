"""Tests for citation-key disambiguation in ReferenceList.add_reference.

Regression for review issue 2.4 (2026-06-04): same-author same-year
papers used to produce identical citation keys (e.g. ``Zhang2024``
twice), which broke BibTeX export and confused inline ``[@Key]``
resolution. The fix is in ``ReferenceList.add_reference``: when a
collision is detected the new key is suffixed ``a``/``b``/``c``/…
"""

from __future__ import annotations

import pytest

from app.models import Paper
from app.models.references import Reference, ReferenceList, ReferenceSource


def _paper(pid: str, first_author: str, year: int | None) -> Paper:
    return Paper(
        id=pid,
        title=f"Paper {pid}",
        authors=[first_author, "Other Author"],
        year=year,
        venue="Test",
    )


def _ref(paper: Paper) -> Reference:
    return Reference.from_paper(paper, ReferenceSource.SEARCH)


# --- No collision baseline -----------------------------------------------


def test_no_collision_keeps_base_key():
    rl = ReferenceList(project_id="p1")
    rl.add_reference(_ref(_paper("p1", "Alice Smith", 2024)))
    assert rl.references[0].citation_key == "Smith2024"


def test_distinct_authors_get_distinct_keys():
    rl = ReferenceList(project_id="p1")
    rl.add_reference(_ref(_paper("p1", "Alice Smith", 2024)))
    rl.add_reference(_ref(_paper("p2", "Bob Jones", 2024)))
    assert {r.citation_key for r in rl.references} == {"Smith2024", "Jones2024"}


def test_distinct_years_get_distinct_keys():
    rl = ReferenceList(project_id="p1")
    rl.add_reference(_ref(_paper("p1", "Alice Smith", 2023)))
    rl.add_reference(_ref(_paper("p2", "Alice Smith", 2024)))
    assert {r.citation_key for r in rl.references} == {"Smith2023", "Smith2024"}


# --- Collision: simple a/b/c suffix --------------------------------------


def test_collision_appends_a():
    rl = ReferenceList(project_id="p1")
    rl.add_reference(_ref(_paper("p1", "Alice Smith", 2024)))
    rl.add_reference(_ref(_paper("p2", "Alice Smith", 2024)))
    keys = [r.citation_key for r in rl.references]
    assert keys == ["Smith2024", "Smith2024a"]


def test_third_collision_appends_b():
    rl = ReferenceList(project_id="p1")
    rl.add_reference(_ref(_paper("p1", "Alice Smith", 2024)))
    rl.add_reference(_ref(_paper("p2", "Alice Smith", 2024)))
    rl.add_reference(_ref(_paper("p3", "Alice Smith", 2024)))
    keys = [r.citation_key for r in rl.references]
    assert keys == ["Smith2024", "Smith2024a", "Smith2024b"]


def test_existing_keys_are_never_renamed():
    """The first inserted reference keeps its base key even when later
    insertions get suffixed."""
    rl = ReferenceList(project_id="p1")
    rl.add_reference(_ref(_paper("p1", "Alice Smith", 2024)))
    first_key = rl.references[0].citation_key
    rl.add_reference(_ref(_paper("p2", "Alice Smith", 2024)))
    rl.add_reference(_ref(_paper("p3", "Alice Smith", 2024)))
    assert rl.references[0].citation_key == first_key == "Smith2024"


def test_collision_gap_fills_correctly():
    """If the user inserts in a different order (base, gap-fill), the
    disambiguator must still find an unused suffix."""
    rl = ReferenceList(project_id="p1")
    rl.add_reference(_ref(_paper("p1", "Alice Smith", 2024)))   # Smith2024
    rl.add_reference(_ref(_paper("p2", "Alice Smith", 2024)))   # Smith2024a
    rl.add_reference(_ref(_paper("p3", "Alice Smith", 2024)))   # Smith2024b
    rl.add_reference(_ref(_paper("p4", "Alice Smith", 2024)))   # Smith2024c
    keys = [r.citation_key for r in rl.references]
    assert keys == ["Smith2024", "Smith2024a", "Smith2024b", "Smith2024c"]


# --- No-author / no-year edge cases -------------------------------------


def test_no_author_yields_unknown_prefix():
    rl = ReferenceList(project_id="p1")
    rl.add_reference(_ref(_paper("p1", "", 2024)))
    rl.add_reference(_ref(_paper("p2", "", 2024)))
    keys = [r.citation_key for r in rl.references]
    assert keys == ["Unknown2024", "Unknown2024a"]


def test_no_year_years_as_xxxx():
    rl = ReferenceList(project_id="p1")
    rl.add_reference(_ref(_paper("p1", "Alice Smith", None)))
    rl.add_reference(_ref(_paper("p2", "Alice Smith", None)))
    keys = [r.citation_key for r in rl.references]
    assert keys == ["SmithXXXX", "SmithXXXXa"]


# --- Duplicates by paper id are still rejected --------------------------


def test_same_paper_id_is_still_a_duplicate():
    """Disambiguation is for citation keys, not paper IDs. Adding the
    same paper twice must still return False (no second copy)."""
    rl = ReferenceList(project_id="p1")
    rl.add_reference(_ref(_paper("p1", "Alice Smith", 2024)))
    result = rl.add_reference(_ref(_paper("p1", "Alice Smith", 2024)))
    assert result is False
    assert len(rl.references) == 1


# --- BibTeX export uses the disambiguated keys -------------------------


def test_bibtex_export_uses_unique_keys():
    """The whole point of disambiguation: BibTeX output must have
    unique ``@article{key,...}`` entries."""
    rl = ReferenceList(project_id="p1")
    rl.add_reference(_ref(_paper("p1", "Alice Smith", 2024)))
    rl.add_reference(_ref(_paper("p2", "Alice Smith", 2024)))
    bib = rl.to_bibtex()
    # Both keys must appear; no duplicate @article{...,
    assert bib.count("@article{Smith2024,") == 1
    assert bib.count("@article{Smith2024a,") == 1
    assert bib.count("@article{") == 2


# --- Stress: suffixes past 'z' fall back gracefully ----------------------


def test_suffix_fallback_past_single_letters(monkeypatch):
    """If a list already has keys Smith2024a..z, the next collision
    should land on Smith2024aa (or the ``_dupN`` tail, depending on
    which is found first)."""
    from app.models import references as refs_module

    rl = ReferenceList(project_id="p1")
    rl.add_reference(_ref(_paper("p1", "Alice Smith", 2024)))  # Smith2024
    # Manually inject 26 pre-existing suffixed keys to simulate
    # exhaustion of single-letter suffixes.
    for i in range(26):
        suffix = chr(ord("a") + i)
        rl.references.append(
            Reference(
                id=f"ref_synth_{i}",
                paper=_paper(f"synth_{i}", "Alice Smith", 2024),
                citation_key=f"Smith2024{suffix}",
                source=ReferenceSource.SEARCH,
            )
        )
    # Now insert a real 28th collision — suffix generator must move on.
    rl.add_reference(_ref(_paper("p28", "Alice Smith", 2024)))
    new_keys = [r.citation_key for r in rl.references if r.id == "ref_p28"]
    assert new_keys and new_keys[0] not in {
        f"Smith2024{chr(ord('a') + i)}" for i in range(26)
    }
    assert new_keys[0].startswith("Smith2024")
