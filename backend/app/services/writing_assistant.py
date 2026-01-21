"""
AI Writing Assistant Service
Provides AI-assisted paper writing with search and generation capabilities
"""
import asyncio
import logging
import re
from typing import List, Optional
from datetime import datetime
from openai import AsyncOpenAI

from ..models import Paper
from ..models.references import Reference, WritingContext, ChatMessage, ReferenceSource
from ..services.paper_search_service import paper_search_service, SearchFilters
from ..config import settings

logger = logging.getLogger(__name__)


# System prompt for writing assistant
WRITING_ASSISTANT_SYSTEM_PROMPT = """你是一位专业的学术论文写作AI助手。你的任务是帮助用户撰写高质量的学术论文。

你的能力包括：
1. 根据用户需求搜索相关学术论文
2. 基于已有的文献综述和参考文献生成论文内容
3. 帮助用户修改和优化论文的特定部分
4. 提供学术写作建议

规则：
- 使用Markdown格式输出
- 引用文献时使用 [@引用键] 格式
- 保持学术论文的正式语言风格
- 如果用户请求搜索论文，你应该提取关键词并说明你将搜索什么内容
- 如果用户请求生成内容，确保内容与已有参考文献相关联

当识别到用户想要搜索论文时，请在回复中包含：
[SEARCH_QUERY: 你提取的搜索关键词]

这样系统会自动执行搜索并返回结果给用户选择。
"""


class WritingAssistantService:
    """
    AI-powered writing assistant for academic paper writing
    """
    
    def __init__(self):
        self.llm_client: Optional[AsyncOpenAI] = None
        self.model: str = settings.ai_model
        self.search_service = paper_search_service
        self.request_timeout = 90.0
        
        # Initialize LLM client if API key available in settings
        if settings.siliconflow_api_key:
            self.llm_client = AsyncOpenAI(
                api_key=settings.siliconflow_api_key,
                base_url=settings.ai_base_url,
                timeout=self.request_timeout,
            )
    
    def configure_llm(self, api_key: str, model: str, base_url: str = None):
        """Configure the LLM client"""
        self.llm_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.request_timeout,
        )
        self.model = model
        logger.info(f"Writing assistant LLM configured: {model}")
    
    async def chat(
        self,
        message: str,
        context: WritingContext,
        history: Optional[List[ChatMessage]] = None
    ) -> ChatMessage:
        """
        Process a chat message from the user
        
        Args:
            message: User's message
            context: Current writing context
            history: Previous chat messages
            
        Returns:
            Assistant's response as ChatMessage
        """
        if not self.llm_client:
            raise ValueError("LLM client not configured. Please configure AI settings first.")
        
        # Build messages for LLM
        messages = [
            {"role": "system", "content": self._build_system_prompt(context)}
        ]
        
        # Add history
        if history:
            for msg in history[-10:]:  # Keep last 10 messages
                messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
        
        # Add current message
        messages.append({"role": "user", "content": message})
        
        try:
            response = await asyncio.wait_for(
                self.llm_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=2000,
                ),
                timeout=self.request_timeout,
            )
            
            assistant_content = response.choices[0].message.content
            
            # Check if there's a search query in the response
            search_query = self._extract_search_query(assistant_content)
            paper_suggestions = None
            action_type = None
            
            if search_query:
                # Execute the search
                action_type = "search"
                papers = await self.search_and_suggest(search_query, context.current_document)
                paper_suggestions = papers[:5]  # Limit to 5 suggestions
                
                # Clean the search query marker from response
                assistant_content = re.sub(
                    r'\[SEARCH_QUERY:.*?\]', 
                    f'我已为您搜索到 {len(paper_suggestions)} 篇相关论文，请查看下方的搜索结果。', 
                    assistant_content
                )
            
            return ChatMessage(
                role="assistant",
                content=assistant_content,
                timestamp=datetime.utcnow(),
                paper_suggestions=paper_suggestions,
                action_type=action_type
            )
            
        except asyncio.TimeoutError as e:
            logger.error("Writing assistant chat timeout")
            raise TimeoutError("AI response timed out") from e
        except Exception as e:
            logger.error(f"Writing assistant chat error: {e}")
            raise
    
    def _build_system_prompt(self, context: WritingContext) -> str:
        """Build the system prompt with context"""
        prompt = WRITING_ASSISTANT_SYSTEM_PROMPT
        
        # Add context information
        if context.references:
            ref_lines = []
            for ref in context.references[:10]:
                paper = ref.paper
                title = paper.title or "Unknown Title"
                authors = ", ".join(paper.authors[:6]) if paper.authors else "Unknown Authors"
                year = str(paper.year) if paper.year else "Unknown Year"
                url = paper.url or ""
                abstract = (paper.abstract or "").strip()
                if len(abstract) > 600:
                    abstract = abstract[:600] + "..."
                line = (
                    f"[@{ref.citation_key}] {title} ({year})\n"
                    f"Authors: {authors}\n"
                    f"URL: {url}\n"
                )
                if abstract:
                    line += f"Abstract: {abstract}\n"
                ref_lines.append(line.strip())
            prompt += "\n\n当前可用参考文献（含摘要/链接）：\n" + "\n\n".join(ref_lines)
        
        if context.literature_review:
            # Add summary of literature review
            review_preview = context.literature_review[:1000]
            prompt += f"\n\n文献综述摘要：\n{review_preview}..."
        
        if context.topic:
            prompt += f"\n\n论文主题：{context.topic}"
        
        return prompt
    
    def _extract_search_query(self, content: str) -> Optional[str]:
        """Extract search query from assistant's response"""
        match = re.search(r'\[SEARCH_QUERY:\s*(.+?)\]', content)
        if match:
            return match.group(1).strip()
        return None
    
    async def search_and_suggest(
        self,
        topic: str,
        current_content: Optional[str] = None,
        limit: int = 10
    ) -> List[Paper]:
        """
        Search for papers based on topic and current content
        
        Args:
            topic: Search topic/keywords
            current_content: Optional current document content
            limit: Maximum number of results
            
        Returns:
            List of suggested papers
        """
        return await self.search_service.search_for_writing(
            topic=topic,
            context=current_content,
            limit=limit
        )
    
    async def generate_section(
        self,
        section_type: str,
        references: List[Reference],
        context: Optional[str] = None,
        outline: Optional[str] = None
    ) -> str:
        """
        Generate a specific section of the paper
        
        Args:
            section_type: Type of section ('introduction', 'methodology', 'discussion', 'conclusion')
            references: Available references to cite
            context: Optional context from previous sections
            outline: Optional outline for this section
            
        Returns:
            Generated section content in Markdown
        """
        if not self.llm_client:
            raise ValueError("LLM client not configured")
        
        section_prompts = {
            "introduction": "撰写论文的引言部分，介绍研究背景、问题陈述和研究目标。",
            "methodology": "撰写研究方法部分，描述所使用的方法、技术和实验设计。",
            "discussion": "撰写讨论部分，分析实验结果，与现有研究进行对比。",
            "conclusion": "撰写结论部分，总结主要发现，讨论研究局限性和未来工作方向。",
            "related_work": "撰写相关工作部分，综述与本研究相关的已有研究成果。",
        }
        
        section_instruction = section_prompts.get(section_type, f"撰写{section_type}部分。")
        
        # Build reference list for prompt
        ref_info = "\n".join([
            f"- [{ref.citation_key}] {ref.paper.title} ({ref.paper.year})"
            for ref in references[:15]
        ])
        
        prompt = f"""请{section_instruction}

## 可用参考文献
{ref_info}

## 上下文
{context or '这是论文的第一部分。'}

## 大纲提示
{outline or '请根据学术论文惯例组织内容。'}

## 要求
1. 使用Markdown格式
2. 适当引用参考文献，使用 [@引用键] 格式
3. 保持学术语言风格
4. 内容详实，有理有据

请生成该部分内容：
"""
        
        try:
            response = await asyncio.wait_for(
                self.llm_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "你是一位专业的学术论文写作助手。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=3000,
                ),
                timeout=self.request_timeout,
            )
            
            return response.choices[0].message.content
            
        except asyncio.TimeoutError as e:
            logger.error("Writing assistant generate_section timeout")
            raise TimeoutError("AI response timed out") from e
        except Exception as e:
            logger.error(f"Failed to generate section: {e}")
            raise
    
    async def expand_content(
        self,
        content: str,
        instruction: str,
        references: List[Reference]
    ) -> str:
        """
        Expand or modify existing content based on instruction
        
        Args:
            content: Current content to expand
            instruction: User's instruction for expansion
            references: Available references
            
        Returns:
            Expanded content
        """
        if not self.llm_client:
            raise ValueError("LLM client not configured")
        
        ref_keys = ", ".join([f"[@{r.citation_key}]" for r in references[:10]])
        
        prompt = f"""请根据以下指令修改/扩展内容。

## 当前内容
{content}

## 修改指令
{instruction}

## 可用引用
{ref_keys}

请输出修改后的完整内容（保持Markdown格式）：
"""
        
        try:
            response = await asyncio.wait_for(
                self.llm_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "你是一位专业的学术论文写作助手。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=3000,
                ),
                timeout=self.request_timeout,
            )
            
            return response.choices[0].message.content
            
        except asyncio.TimeoutError as e:
            logger.error("Writing assistant expand_content timeout")
            raise TimeoutError("AI response timed out") from e
        except Exception as e:
            logger.error(f"Failed to expand content: {e}")
            raise
    
    def create_reference_from_paper(
        self,
        paper: Paper,
        source: ReferenceSource = ReferenceSource.SEARCH
    ) -> Reference:
        """Create a Reference object from a Paper"""
        return Reference.from_paper(paper, source)


# Singleton instance
writing_assistant = WritingAssistantService()
