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
from .llm_factory import create_llm_client, configure_llm_client

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

# Context-overflow budget. The system prompt can balloon to 30K+ tokens
# with 50 references, which is more than the model's input window. The
# budget is split across the reference block and the literature-review
# preview so neither can crowd the other out. Truncation is best-effort:
# we keep the most informative fields (title, year, authors, URL) and
# drop the abstract first, then shorten the longest entries when even
# the trimmed list is over budget.
MAX_PROMPT_TOKENS = 8_000
MAX_ABSTRACT_CHARS = 200
CHARS_PER_TOKEN = 4  # rough heuristic; good enough for budget control


class WritingAssistantService:
    """
    AI-powered writing assistant for academic paper writing
    """

    def __init__(self):
        self.llm_client: Optional[AsyncOpenAI] = None
        self.model: str = settings.ai_model
        self.search_service = paper_search_service
        self.request_timeout = 90.0

        self.llm_client = create_llm_client(timeout=self.request_timeout)

    def configure_llm(self, api_key: str, model: str, base_url: Optional[str] = None):
        """Configure the LLM client"""
        configure_llm_client(self, api_key, model, base_url or "", self.request_timeout)

    async def chat(
        self,
        message: str,
        context: WritingContext,
        history: Optional[List[ChatMessage]] = None,
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
            raise ValueError(
                "LLM client not configured. Please configure AI settings first."
            )

        # Build messages for LLM
        messages = [{"role": "system", "content": self._build_system_prompt(context)}]

        # Add history
        if history:
            for msg in history[-10:]:  # Keep last 10 messages
                messages.append({"role": msg.role, "content": msg.content})

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

            assistant_content = response.choices[0].message.content or ""

            # Check if there's a search query in the response
            search_query = self._extract_search_query(assistant_content)
            paper_suggestions: Optional[List[Paper]] = None
            action_type: Optional[str] = None

            if search_query:
                # Execute the search
                action_type = "search"
                papers = await self.search_and_suggest(
                    search_query, context.current_document
                )
                paper_suggestions = papers[:5]  # Limit to 5 suggestions

                # Clean the search query marker from response
                assistant_content = re.sub(
                    r"\[SEARCH_QUERY:.*?\]",
                    f"我已为您搜索到 {len(paper_suggestions)} 篇相关论文，请查看下方的搜索结果。",
                    assistant_content,
                )

            return ChatMessage(
                role="assistant",
                content=assistant_content,
                timestamp=datetime.utcnow(),
                paper_suggestions=paper_suggestions,
                action_type=action_type,
            )

        except asyncio.TimeoutError as e:
            logger.error("Writing assistant chat timeout")
            raise TimeoutError("AI response timed out") from e
        except Exception as e:
            logger.error(f"Writing assistant chat error: {e}")
            raise

    def _build_system_prompt(self, context: WritingContext) -> str:
        """Build the system prompt with context, capped at
        ``MAX_PROMPT_TOKENS`` to avoid blowing past the model's input
        window when the user has many references or a long literature
        review.
        """
        prompt = WRITING_ASSISTANT_SYSTEM_PROMPT
        ref_block = ""
        review_block = ""
        topic_block = ""

        if context.references:
            ref_lines = []
            for ref in context.references[:50]:
                paper = ref.paper
                title = paper.title or "Unknown Title"
                authors = (
                    ", ".join(paper.authors[:6]) if paper.authors else "Unknown Authors"
                )
                year = str(paper.year) if paper.year else "Unknown Year"
                url = paper.url or ""
                abstract = (paper.abstract or "").strip()
                if len(abstract) > MAX_ABSTRACT_CHARS:
                    abstract = abstract[:MAX_ABSTRACT_CHARS] + "..."
                line = (
                    f"[@{ref.citation_key}] {title} ({year})\n"
                    f"Authors: {authors}\n"
                    f"URL: {url}\n"
                )
                if abstract:
                    line += f"Abstract: {abstract}\n"
                ref_lines.append(line.strip())
            ref_block = "\n\n当前可用参考文献（含摘要/链接）：\n" + "\n\n".join(ref_lines)

        if context.literature_review:
            review_preview = context.literature_review[:1000]
            review_block = f"\n\n文献综述摘要：\n{review_preview}..."

        if context.topic:
            topic_block = f"\n\n论文主题：{context.topic}"

        # If the assembly is already over budget, drop review + topic
        # (lowest-priority context) and start trimming references
        # from the tail. Per-abstract caps happen at construction
        # time above.
        prompt += ref_block + review_block + topic_block
        if self._estimate_tokens(prompt) > MAX_PROMPT_TOKENS:
            prompt, was_truncated = self._truncate_to_budget(
                prompt, ref_block, review_block, topic_block
            )
            if was_truncated:
                logger.warning(
                    "writing_assistant: system prompt truncated to fit "
                    "%d-token budget (had %d references, review_len=%d)",
                    MAX_PROMPT_TOKENS,
                    len(context.references or []),
                    len(context.literature_review or ""),
                )
        return prompt

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Cheap token estimate: ``chars / CHARS_PER_TOKEN``.

        A real tokenizer is overkill here; the goal is to detect
        catastrophic overflow, not to fit the model exactly.
        """
        if not text:
            return 0
        return max(1, len(text) // CHARS_PER_TOKEN)

    @staticmethod
    def _truncate_to_budget(
        prompt: str,
        ref_block: str,
        review_block: str,
        topic_block: str,
    ) -> tuple[str, bool]:
        """Shrink the optional context blocks until the total fits
        ``MAX_PROMPT_TOKENS``.

        Strategy (in order):

        1. Drop the literature-review preview (lowest value per token).
        2. Drop the topic line.
        3. Halve the reference block; if still over, halve again.

        Returns the rewritten prompt and a flag indicating whether
        anything was actually trimmed.
        """
        was_truncated = False
        budget = MAX_PROMPT_TOKENS
        base = WRITING_ASSISTANT_SYSTEM_PROMPT

        review = review_block
        topic = topic_block
        refs = ref_block

        if WritingAssistantService._estimate_tokens(base + refs + review + topic) <= budget:
            return prompt, False

        # 1) Drop review block
        review = ""
        was_truncated = True
        if WritingAssistantService._estimate_tokens(base + refs + review + topic) <= budget:
            return base + refs + review + topic, True

        # 2) Drop topic block
        topic = ""
        if WritingAssistantService._estimate_tokens(base + refs + review) <= budget:
            return base + refs + review, True

        # 3) Iteratively halve the reference block
        while refs and WritingAssistantService._estimate_tokens(base + refs) > budget:
            refs = refs[: max(0, len(refs) // 2)]
            was_truncated = True

        return base + refs, was_truncated

    @staticmethod
    def _cap_prompt_block(block: str, max_tokens: int) -> str:
        """Truncate a single prompt block to ``max_tokens`` using the
        same ``chars / 4`` heuristic. Returns the original block when
        it already fits, otherwise a string trimmed to the budget and
        flagged with a trailing ``...`` marker. Used by
        ``generate_section`` and ``expand_content`` for their inline
        reference lists.
        """
        if not block:
            return block
        cap = max(1, int(max_tokens) * CHARS_PER_TOKEN)
        if len(block) <= cap:
            return block
        logger.warning(
            "writing_assistant: prompt block truncated to %d chars "
            "(was %d)",
            cap,
            len(block),
        )
        return block[:cap] + "\n..."

    def _extract_search_query(self, content: str) -> Optional[str]:
        """Extract search query from assistant's response"""
        match = re.search(r"\[SEARCH_QUERY:\s*(.+?)\]", content)
        if match:
            return match.group(1).strip()
        return None

    async def search_and_suggest(
        self, topic: str, current_content: Optional[str] = None, limit: int = 10
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
            topic=topic, context=current_content, limit=limit
        )

    async def generate_section(
        self,
        section_type: str,
        references: List[Reference],
        context: Optional[str] = None,
        outline: Optional[str] = None,
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

        section_instruction = section_prompts.get(
            section_type, f"撰写{section_type}部分。"
        )

        # Build reference list for prompt. Cap at 15 references and
        # then enforce the same token budget the system prompt uses so
        # a 50-reference call doesn't blow up the request.
        ref_info = "\n".join(
            [
                f"- [{ref.citation_key}] {ref.paper.title} ({ref.paper.year})"
                for ref in references[:15]
            ]
        )
        ref_info = self._cap_prompt_block(ref_info, MAX_PROMPT_TOKENS // 2)

        prompt = f"""请{section_instruction}

## 可用参考文献
{ref_info}

## 上下文
{context or "这是论文的第一部分。"}

## 大纲提示
{outline or "请根据学术论文惯例组织内容。"}

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
                        {
                            "role": "system",
                            "content": "你是一位专业的学术论文写作助手。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.7,
                    max_tokens=3000,
                ),
                timeout=self.request_timeout,
            )

            return response.choices[0].message.content or ""

        except asyncio.TimeoutError as e:
            logger.error("Writing assistant generate_section timeout")
            raise TimeoutError("AI response timed out") from e
        except Exception as e:
            logger.error(f"Failed to generate section: {e}")
            raise

    async def expand_content(
        self, content: str, instruction: str, references: List[Reference]
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
                        {
                            "role": "system",
                            "content": "你是一位专业的学术论文写作助手。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.7,
                    max_tokens=3000,
                ),
                timeout=self.request_timeout,
            )

            return response.choices[0].message.content or ""

        except asyncio.TimeoutError as e:
            logger.error("Writing assistant expand_content timeout")
            raise TimeoutError("AI response timed out") from e
        except Exception as e:
            logger.error(f"Failed to expand content: {e}")
            raise

    def create_reference_from_paper(
        self, paper: Paper, source: ReferenceSource = ReferenceSource.SEARCH
    ) -> Reference:
        """Create a Reference object from a Paper"""
        return Reference.from_paper(paper, source)


# Singleton instance
writing_assistant = WritingAssistantService()
