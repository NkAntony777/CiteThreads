"""
Smart Citation Intent Classifier
Optimized for robustness and accuracy using LLM only.
Embedding pre-filtering has been removed as per user request.
"""
import logging
import json
import asyncio
import re
from typing import Optional, List, Tuple
from dataclasses import dataclass
from openai import AsyncOpenAI

from ..models import Paper, CitationIntent, IntentClassificationResult, CitationFunction, CitationSentiment
from ..config import settings

logger = logging.getLogger(__name__)



# Deep Insight Analysis Prompt (Text Format)
CLASSIFICATION_PROMPT_DEEP_INSIGHT = """Analyze the citation relationship between the following two papers.
You act as a senior academic researcher conducting a deep rhetorical analysis of citations.

Citing Paper: {citing_title}
Citing Abstract: {citing_abstract}

Cited Paper: {cited_title}
Cited Abstract: {cited_abstract}

【CRITICAL】Citation Context (Evidence):
"{citation_context}"

Task:
1. Classify the INTENT (SUPPORT/OPPOSE/NEUTRAL).
2. Determine the FUNCTION (BACKGROUND/METHODOLOGY/COMPARISON/CRITIQUE/BASIS).
3. Determine the SENTIMENT (POSITIVE/NEUTRAL/NEGATIVE).
4. Extract the KEY CONCEPT borrowed or discussed.
5. Rate importance (1-5).

Output STRICT Key-Value Text Format (No JSON, No Markdown):
INTENT: [SUPPORT/OPPOSE/NEUTRAL]
CONFIDENCE: [0.0-1.0]
REASONING: [Brief explanation]
FUNCTION: [BACKGROUND/METHODOLOGY/COMPARISON/CRITIQUE/BASIS]
SENTIMENT: [POSITIVE/NEUTRAL/NEGATIVE]
IMPORTANCE: [1-5]
KEY_CONCEPT: [Concept Name]
"""


# Optimized prompt using ONLY title + abstract (Text Format)
CLASSIFICATION_PROMPT_V2 = """分析两篇论文的引用关系，判断引用意图。

引用方: {citing_title}
摘要: {citing_abstract}

被引方: {cited_title}
摘要: {cited_abstract}

引用意图分类:
- SUPPORT: 引用方采用/扩展/验证了被引方的方法或理论
- OPPOSE: 引用方质疑/反驳/修正了被引方的观点
- NEUTRAL: 仅作为背景提及，无直接学术关系

请输出严格的键值对格式:
INTENT: [SUPPORT/OPPOSE/NEUTRAL]
CONFIDENCE: [0.0-1.0]
REASONING: [原因简述]
"""

# Prompt with explicit Citation Context (Text Format)
CLASSIFICATION_PROMPT_WITH_CONTEXT = """分析引用意图。

引用方: {citing_title}
被引方: {cited_title}

【关键】引用原文上下文 (Context):
"{citation_context}"

请输出严格的键值对格式:
INTENT: [SUPPORT/OPPOSE/NEUTRAL]
CONFIDENCE: [0.0-1.0]
REASONING: [基于上下文的原因]
"""

# Ultra-short prompt for title-only classification (Text Format)
CLASSIFICATION_PROMPT_TITLE_ONLY = """判断论文引用意图:
引用方: {citing_title} ({citing_year})
被引方: {cited_title} ({cited_year})

请输出严格的键值对格式:
INTENT: [SUPPORT/OPPOSE/NEUTRAL]
CONFIDENCE: [0.0-1.0]
REASONING: [原因]
"""


@dataclass
class ClassificationStats:
    """Statistics for a classification batch"""
    total: int = 0
    llm_classified: int = 0
    errors: int = 0


class SmartCitationClassifier:
    """
    Smart citation intent classifier using LLM only.
    Uses robust text parsing instead of JSON.
    """
    
    def __init__(self):
        self.llm_client: Optional[AsyncOpenAI] = None
        self.llm_model: str = settings.ai_model
        self._cache: dict = {}
        self._stats = ClassificationStats()
        
        # Initialize LLM client if API key available
        if settings.siliconflow_api_key:
            self.llm_client = AsyncOpenAI(
                api_key=settings.siliconflow_api_key,
                base_url=settings.ai_base_url
            )
    
    def configure_llm(self, api_key: str, model: str, base_url: str):
        """Configure custom LLM for classification"""
        self.llm_client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.llm_model = model
        logger.info(f"LLM configured: model={model}")
    
    def reset_stats(self):
        """Reset classification statistics"""
        self._stats = ClassificationStats()
    
    def get_stats(self) -> ClassificationStats:
        """Get current classification statistics"""
        return self._stats
    
    async def classify(self, citing: Paper, cited: Paper, contexts: List[str] = None) -> IntentClassificationResult:
        """
        Classify citation intent using LLM.
        """
        cache_key = f"{citing.id}|{cited.id}"
        if contexts and cache_key in self._cache:
             pass
        elif cache_key in self._cache:
            return self._cache[cache_key]
        
        self._stats.total += 1
        
        result = await self._classify_with_llm(citing, cited, contexts)
        self._stats.llm_classified += 1
        self._cache[cache_key] = result
        return result
    
    async def classify_batch(
        self,
        paper_pairs: List[Tuple[Paper, Paper, Optional[List[str]]]], # Added contexts to tuple
        progress_callback: Optional[callable] = None
    ) -> List[IntentClassificationResult]:
        """
        Classify multiple citation pairs using LLM.
        """
        if not paper_pairs:
            return []
        
        self.reset_stats()
        results = [None] * len(paper_pairs)
        llm_queue = []
        
        for i, (citing, cited, contexts) in enumerate(paper_pairs):
            cache_key = f"{citing.id}|{cited.id}"
            
            if cache_key in self._cache and not contexts: # Use cache if no new context to leverage
                results[i] = self._cache[cache_key]
                continue
            
            self._stats.total += 1
            llm_queue.append((i, citing, cited, contexts))
        
        # Process LLM queue with concurrency limit
        if llm_queue:
            semaphore = asyncio.Semaphore(5)  # Moderate concurrency
            
            async def classify_one(idx: int, citing: Paper, cited: Paper, ctx: Optional[List[str]]):
                async with semaphore:
                    try:
                        result = await self._classify_with_llm(citing, cited, ctx)
                    except Exception as e:
                        logger.error(f"Classification error: {e}")
                        result = IntentClassificationResult(
                            intent=CitationIntent.UNKNOWN, 
                            confidence=0.0, 
                            reasoning=f"Error: {str(e)}"
                        )
                    return idx, result
            
            tasks = [classify_one(idx, citing, cited, ctx) for idx, citing, cited, ctx in llm_queue]
            llm_results = await asyncio.gather(*tasks)
            
            for idx, result in llm_results:
                results[idx] = result
                self._stats.llm_classified += 1
                citing, cited, _ = paper_pairs[idx]
                caching_key = f"{citing.id}|{cited.id}"
                self._cache[caching_key] = result
                
                if progress_callback:
                    progress_callback(len([r for r in results if r is not None]), len(paper_pairs))
        
        return results

    def _extract_field(self, text: str, key: str, default: str = "") -> str:
        """Helper to extract value from Key: Value format"""
        # Case insensitive match for KEY: ...
        pattern = f"{key}\\s*[:：]\\s*(.*)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return default

    async def _classify_with_llm(self, citing: Paper, cited: Paper, contexts: List[str] = None) -> IntentClassificationResult:
        """Classify using LLM with context or abstract"""
        if not self.llm_client:
            return IntentClassificationResult(
                intent=CitationIntent.UNKNOWN,
                confidence=0.0,
                reasoning="LLM not configured"
            )
        
        # Determine strictness and prompt based on data
        use_deep_insight = False
        
        if contexts and len(contexts) > 0:
            use_deep_insight = True
            # Join top 3 contexts
            context_text = "\n...\n".join(contexts[:3])
            prompt = CLASSIFICATION_PROMPT_DEEP_INSIGHT.format(
                citing_title=citing.title,
                citing_abstract=citing.abstract[:300] or "No Abstract",
                cited_title=cited.title,
                cited_abstract=cited.abstract[:300] or "No Abstract",
                citation_context=context_text
            )
        elif citing.abstract and cited.abstract:
            prompt = CLASSIFICATION_PROMPT_V2.format(
                citing_title=citing.title,
                citing_abstract=citing.abstract[:500] or "无摘要",
                cited_title=cited.title,
                cited_abstract=cited.abstract[:500] or "无摘要"
            )
        else:
            prompt = CLASSIFICATION_PROMPT_TITLE_ONLY.format(
                citing_title=citing.title,
                citing_year=citing.year or "?",
                cited_title=cited.title,
                cited_year=cited.year or "?"
            )
        
        try:
            response = await self.llm_client.chat.completions.create(
                model=self.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=300
                # removed json_object response format
            )
            
            content = response.choices[0].message.content.strip()
            
            # Robust Text Extraction
            intent_str = self._extract_field(content, "INTENT", "NEUTRAL").upper()
            confidence_str = self._extract_field(content, "CONFIDENCE", "0.7")
            reasoning = self._extract_field(content, "REASONING", "")
            
            # Basic cleanup/parsing
            intent_map = {
                "SUPPORT": CitationIntent.SUPPORT,
                "OPPOSE": CitationIntent.OPPOSE,
                "NEUTRAL": CitationIntent.NEUTRAL
            }
            # Handle potential extra chars in intent (e.g. "SUPPORT.")
            intent_clean = re.sub(r'[^A-Z]', '', intent_str)
            intent = intent_map.get(intent_clean, intent_map.get(intent_str, CitationIntent.NEUTRAL))
            
            try:
                # Extract float even if there are other chars
                conf_match = re.search(r'0\.\d+|1\.0|0', confidence_str)
                confidence = float(conf_match.group(0)) if conf_match else 0.7
            except ValueError:
                confidence = 0.7

            result = IntentClassificationResult(
                intent=intent,
                confidence=confidence,
                reasoning=reasoning
            )
            
            # Extract Deep Insight Fields if available
            if use_deep_insight:
                # Function
                func_str = self._extract_field(content, "FUNCTION", "UNKNOWN").upper()
                func_map = {k: v for k, v in CitationFunction.__members__.items()} 
                # Fuzzy match function key
                func_key = next((k for k in func_map if k in func_str), "UNKNOWN")
                result.citation_function = func_map.get(func_key, CitationFunction.UNKNOWN)
                
                # Sentiment
                sent_str = self._extract_field(content, "SENTIMENT", "UNKNOWN").upper()
                sent_map = {k: v for k, v in CitationSentiment.__members__.items()}
                sent_key = next((k for k in sent_map if k in sent_str), "UNKNOWN")
                result.citation_sentiment = sent_map.get(sent_key, CitationSentiment.UNKNOWN)
                
                imp_str = self._extract_field(content, "IMPORTANCE", "0")
                try:
                    import_match = re.search(r'[1-5]', imp_str)
                    result.importance_score = int(import_match.group(0)) if import_match else 0
                except ValueError:
                    result.importance_score = 0
                    
                result.key_concept = self._extract_field(content, "KEY_CONCEPT", None)
            
            return result
        
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            logger.debug(f"Failed content was: {content if 'content' in locals() else 'None'}")
            # Return neutral on error, don't crash
            return IntentClassificationResult(
                intent=CitationIntent.NEUTRAL,
                confidence=0.5,
                reasoning=f"Analysis failed: {str(e)}"
            )


# Singleton instances
smart_classifier = SmartCitationClassifier()
intent_classifier = smart_classifier
