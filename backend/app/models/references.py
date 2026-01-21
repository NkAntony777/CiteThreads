"""
Reference Management Models
Data models for managing paper references in AI writing
"""
from pydantic import BaseModel, Field
from typing import Optional, List
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
        """Create a Reference from a Paper"""
        # Generate citation key: FirstAuthorLastName + Year
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
        """Generate a BibTeX-style citation key"""
        # Get first author's last name
        if paper.authors:
            first_author = paper.authors[0]
            # Handle "First Last" or "Last, First" format
            if "," in first_author:
                last_name = first_author.split(",")[0].strip()
            else:
                parts = first_author.split()
                last_name = parts[-1] if parts else "Unknown"
        else:
            last_name = "Unknown"
        
        # Clean last name (remove non-alphanumeric)
        import re
        last_name = re.sub(r'[^a-zA-Z]', '', last_name)
        
        # Add year
        year = paper.year if paper.year else "XXXX"
        
        return f"{last_name}{year}"


class ReferenceList(BaseModel):
    """
    A collection of references for a project
    """
    project_id: str = Field(..., description="Associated project ID")
    references: List[Reference] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    def add_reference(self, ref: Reference) -> bool:
        """Add a reference if not already present"""
        # Check for duplicates by paper ID
        existing_ids = {r.paper.id for r in self.references}
        if ref.paper.id in existing_ids:
            return False
        
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
