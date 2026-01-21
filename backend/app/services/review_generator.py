"""
Literature Review Generator Service
Generates literature reviews based on references and graph structure
"""
import logging
from typing import List, Optional
from datetime import datetime
from openai import AsyncOpenAI

from ..models import Paper, GraphData
from ..models.references import Reference, LiteratureReviewDraft, ReferenceList
from ..config import settings

logger = logging.getLogger(__name__)


# Prompt template for literature review generation
REVIEW_PROMPT_TEMPLATE = """你是一位专业的学术论文写作助手。请根据以下参考文献信息生成一篇结构化的文献综述。

## 参考文献列表
{references_section}

## 引用关系图谱信息
{graph_section}

## 要求
1. 综述应包含以下部分：导言、主题分析、研究趋势、关键发现、总结
2. 使用Markdown格式，包含适当的标题层级
3. 在正文中使用 [@引用键] 格式引用文献，例如 [@Zhang2024]
4. 分析文献之间的关系：哪些相互支持，哪些存在争议
5. 识别研究领域的主要趋势和创新点
6. 总结现有研究的不足和未来研究方向

## 写作风格
{style_instruction}

请生成文献综述：
"""

STYLE_INSTRUCTIONS = {
    "academic": "使用正式的学术语言，注重逻辑性和客观性。",
    "concise": "简洁明了，重点突出，避免冗余。",
    "detailed": "详细深入，对每篇文献进行充分分析，提供丰富的背景信息。",
}


class LiteratureReviewGenerator:
    """
    Generates literature reviews based on references and citation graph structure
    """
    
    def __init__(self):
        self.llm_client: Optional[AsyncOpenAI] = None
        self.model: str = settings.ai_model
        
        # Initialize LLM client if API key available in settings
        if settings.siliconflow_api_key:
            self.llm_client = AsyncOpenAI(
                api_key=settings.siliconflow_api_key,
                base_url=settings.ai_base_url
            )
    
    def configure_llm(self, api_key: str, model: str, base_url: str = None):
        """Configure the LLM client"""
        self.llm_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url
        )
        self.model = model
        logger.info(f"Review generator LLM configured: {model}")
    
    async def generate(
        self,
        references: List[Reference],
        graph_structure: Optional[dict] = None,
        style: str = "academic"
    ) -> LiteratureReviewDraft:
        """
        Generate a literature review based on references and graph structure
        
        Args:
            references: List of Reference objects to include
            graph_structure: Optional graph data with citation relationships
            style: Review style - 'academic', 'concise', or 'detailed'
            
        Returns:
            LiteratureReviewDraft with generated content
        """
        if not references:
            return LiteratureReviewDraft(
                project_id="",
                content="# 文献综述\n\n*请先添加参考文献*",
                references=[],
                style=style
            )
        
        if not self.llm_client:
            logger.error("LLM client not configured")
            raise ValueError("LLM client not configured. Please configure AI settings first.")
        
        # Build the prompt
        prompt = self._build_review_prompt(references, graph_structure, style)
        
        try:
            # Call LLM
            response = await self.llm_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一位专业的学术论文写作助手，擅长撰写高质量的文献综述。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=4000,
            )
            
            content = response.choices[0].message.content
            
            return LiteratureReviewDraft(
                project_id=references[0].paper.id if references else "",
                content=content,
                references=references,
                style=style,
                generated_at=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Failed to generate literature review: {e}")
            raise
    
    def _build_review_prompt(
        self,
        references: List[Reference],
        graph_structure: Optional[dict],
        style: str
    ) -> str:
        """Build the prompt for literature review generation"""
        
        # Build references section
        ref_lines = []
        for i, ref in enumerate(references, 1):
            paper = ref.paper
            authors = ", ".join(paper.authors[:3])
            if len(paper.authors) > 3:
                authors += " et al."
            
            ref_lines.append(f"""
### [{ref.citation_key}] {paper.title}
- **作者**: {authors}
- **年份**: {paper.year or 'N/A'}
- **期刊/会议**: {paper.venue or 'N/A'}
- **摘要**: {paper.abstract[:500] + '...' if paper.abstract and len(paper.abstract) > 500 else paper.abstract or 'N/A'}
- **被引次数**: {paper.citation_count}
""")
        
        references_section = "\n".join(ref_lines)
        
        # Build graph section
        graph_section = self._build_graph_section(references, graph_structure)
        
        # Get style instruction
        style_instruction = STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS["academic"])
        
        return REVIEW_PROMPT_TEMPLATE.format(
            references_section=references_section,
            graph_section=graph_section,
            style_instruction=style_instruction
        )
    
    def _build_graph_section(
        self,
        references: List[Reference],
        graph_structure: Optional[GraphData]
    ) -> str:
        """Build the graph information section for the prompt"""
        if not graph_structure:
            return "未提供引用关系图谱信息。"
        
        lines = []
        ref_paper_ids = {ref.paper.id for ref in references}
        
        # Extract relevant edges from graph
        edges = graph_structure.edges
        nodes = graph_structure.nodes
        
        # Create paper id to citation key mapping
        id_to_key = {ref.paper.id: ref.citation_key for ref in references}
        
        # Find relationships between selected references
        for edge in edges:
            source_id = edge.source
            target_id = edge.target
            
            if source_id in ref_paper_ids and target_id in ref_paper_ids:
                intent = edge.intent
                source_key = id_to_key.get(source_id, source_id)
                target_key = id_to_key.get(target_id, target_id)
                
                if intent == "SUPPORT":
                    lines.append(f"- {source_key} **支持** {target_key} 的研究")
                elif intent == "OPPOSE":
                    lines.append(f"- {source_key} **反驳/质疑** {target_key} 的观点")
                else:
                    lines.append(f"- {source_key} 引用了 {target_key}")
        
        if not lines:
            return "所选文献之间未发现直接引用关系。"
        
        return "文献之间的引用关系：\n" + "\n".join(lines)
    
    async def refine_section(
        self,
        section_content: str,
        instruction: str,
        references: List[Reference]
    ) -> str:
        """
        Refine a specific section of the review based on user instruction
        
        Args:
            section_content: Current section content
            instruction: User's refinement instruction
            references: Available references
            
        Returns:
            Refined section content
        """
        if not self.llm_client:
            raise ValueError("LLM client not configured")
        
        prompt = f"""请根据以下指令修改文献综述的这一部分。

## 当前内容
{section_content}

## 修改指令
{instruction}

## 可用的参考文献
{', '.join([f'[@{ref.citation_key}]' for ref in references])}

请输出修改后的内容（仅输出修改后的部分，保持Markdown格式）：
"""
        
        try:
            response = await self.llm_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一位专业的学术论文写作助手。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=2000,
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"Failed to refine section: {e}")
            raise


# Singleton instance
review_generator = LiteratureReviewGenerator()
