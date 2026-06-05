"""
Reference Management Models
Data models for managing paper references in AI writing
"""
from pydantic import BaseModel, Field
from typing import Iterator, Optional, List
from datetime import datetime
from enum import Enum

from . import Paper


class ReferenceSource(str, Enum):
    """Source of how a reference was added"""
    GRAPH = "graph"  # Selected from citation graph
    SEARCH = "search"  # Found via search
    UPLOAD = "upload"  # Uploaded PDF or URL


class Reference(BaseModel):
    """
    A reference entry for use in literature review/paper writing
    """
    id: str = Field(..., description="Unique reference ID")
    paper: Paper = Field(..., description="The paper being referenced")
    citation_key: str = Field(..., description="BibTeX-style citation key, e.g., 'Zhang2024'")
    added_at: datetime = Field(default_factory=datetime.utcnow)
    source: ReferenceSource = Field(..., description="How this reference was added")
    notes: Optional[str] = Field(None, description="User notes about this reference")

    @classmethod
    def from_paper(cls, paper: Paper, source: ReferenceSource = ReferenceSource.SEARCH, notes: str = None) -> "Reference":
        """Create a Reference from a Paper.

        The citation key here is the *base* key (e.g. ``Zhang2024``).
        It will be disambiguated by ``ReferenceList.add_reference`` if
        another reference in the list already uses the same key.
        """
        citation_key = cls._generate_citation_key(paper)

        return cls(
            id=f"ref_{paper.id}",
            paper=paper,
            citation_key=citation_key,
            source=source,
            notes=notes,
        )

    @staticmethod
    def _generate_citation_key(paper: Paper) -> str:
        """Generate a BibTeX-style citation key.

        Format: ``FirstAuthorLastName + Year`` (e.g. ``Zhang2024``).
        Returns ``UnknownXXXX`` when no author / year is available.
        """
        import re
        if paper.authors:
            first_author = paper.authors[0]
            if "," in first_author:
                last_name = first_author.split(",")[0].strip()
            else:
                parts = first_author.split()
                last_name = parts[-1] if parts else "Unknown"
        else:
            last_name = "Unknown"

        last_name = re.sub(r'[^a-zA-Z]', '', last_name) or "Unknown"
        year = paper.year if paper.year else "XXXX"

        return f"{last_name}{year}"


def _citation_suffixes() -> Iterator[str]:
    """Yield ``a``, ``b``, …, ``z``, ``aa``, ``ab``, …, ``zz``, …"""
    import string
    for ch in string.ascii_lowercase:
        yield ch
    for a in string.ascii_lowercase:
        for b in string.ascii_lowercase:
            yield a + b


class ReferenceList(BaseModel):
    """
    A collection of references for a project
    """
    project_id: str = Field(..., description="Associated project ID")
    references: List[Reference] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def add_reference(self, ref: Reference) -> bool:
        """Add a reference if not already present.

        If the incoming ``ref``'s citation_key collides with one already
        in the list, the incoming key is rewritten with an alphabetic
        suffix (``a``, ``b``, ``c``, …) to keep every key unique within
        the list. Existing references are never renamed — the
        disambiguation only applies to the new entry.
        """
        existing_ids = {r.paper.id for r in self.references}
        if ref.paper.id in existing_ids:
            return False

        existing_keys = {r.citation_key for r in self.references}
        if ref.citation_key in existing_keys:
            ref.citation_key = self._disambiguate_key(
                ref.citation_key, existing_keys
            )

        self.references.append(ref)
        self.updated_at = datetime.utcnow()
        return True

    def remove_reference(self, ref_id: str) -> bool:
        """Remove a reference by ID"""
        original_count = len(self.references)
        self.references = [r for r in self.references if r.id != ref_id]

        if len(self.references) < original_count:
            self.updated_at = datetime.utcnow()
            return True
        return False

    def get_reference(self, ref_id: str) -> Optional[Reference]:
        """Get a reference by ID"""
        for ref in self.references:
            if ref.id == ref_id:
                return ref
        return None

    def to_bibtex(self) -> str:
        """Export references as BibTeX format"""
        entries = []
        for ref in self.references:
            paper = ref.paper
            entry = f"@article{{{ref.citation_key},\n"
            entry += f"  title = {{{paper.title}}},\n"
            if paper.authors:
                entry += f"  author = {{{' and '.join(paper.authors)}}},\n"
            if paper.year:
                entry += f"  year = {{{paper.year}}},\n"
            if paper.venue:
                entry += f"  journal = {{{paper.venue}}},\n"
            if paper.doi:
                entry += f"  doi = {{{paper.doi}}},\n"
            entry += "}"
            entries.append(entry)

        return "\n\n".join(entries)

    @staticmethod
    def _disambiguate_key(base_key: str, existing: set[str]) -> str:
        """Return ``base_key`` or ``base_key + suffix`` that is not in
        ``existing``. Suffixes are lowercase single letters in order:
        ``a``, ``b``, ``c``, …, ``z``, then ``aa``, ``ab``, …. Falls
        back to ``_dupN`` for pathological cases."""
        if base_key not in existing:
            return base_key
        for suffix in _citation_suffixes():
            candidate = f"{base_key}{suffix}"
            if candidate not in existing:
                return candidate
        n = 1
        while f"{base_key}_dup{n}" in existing:
            n += 1
        return f"{base_key}_dup{n}"


class LiteratureReviewDraft(BaseModel):
    """
    A generated literature review draft
    """
    project_id: str
    content: str = Field(..., description="Generated Markdown content")
    references: List[Reference] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    style: str = Field("academic", description="Review style: academic, concise, detailed")

    def get_inline_citations(self) -> List[str]:
        """Extract all inline citation keys from content"""
        import re
        # Match patterns like [@Zhang2024] or [Zhang2024]
        pattern = r'\[@?([A-Za-z]+\d{4}[a-z]?)\]'
        return re.findall(pattern, self.content)

    def remove_citation(self, citation_key: str) -> str:
        """
        Remove all citations of a specific reference from content
        Returns updated content
        """
        import re
        # Remove inline citations
        patterns = [
            rf'\[@?{citation_key}\]',  # [@Key] or [Key]
            rf'\({citation_key}\)',     # (Key)
        ]

        content = self.content
        for pattern in patterns:
            content = re.sub(pattern, '', content)

        # Clean up any double spaces or empty brackets
        content = re.sub(r'\s+', ' ', content)
        content = re.sub(r'\[\s*\]', '', content)

        self.content = content
        return content


class WritingContext(BaseModel):
    """
    Context for AI writing assistance
    """
    project_id: str
    literature_review: Optional[str] = None
    current_document: str = ""
    references: List[Reference] = Field(default_factory=list)
    topic: Optional[str] = None
    outline: Optional[List[str]] = None


class ChatMessage(BaseModel):
    """
    A chat message in the writing assistant
    """
    role: str = Field(..., description="Role: 'user', 'assistant', or 'system'")
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    paper_suggestions: Optional[List[Paper]] = None
    action_type: Optional[str] = None  # 'search', 'generate', 'edit', etc.
