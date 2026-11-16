import re
import time
import json
import asyncio
import urllib.parse
from typing import Optional, List, Dict, Any, Tuple
import requests
from lxml import html
from openai import AsyncOpenAI, APIError, AuthenticationError, RateLimitError
from ddgs import DDGS
from app.core.config import settings
from app.providers.base import BaseProvider, ProviderResponse
from app.providers.registry import model_registry

# ---------------------------------------------------------------------------
# Internal-thought scrubber
# Models like Nemotron-SuperVL emit chain-of-thought inside <think>...</think>
# or plain-text "Let me re-read…" preambles. We strip them before the user
# ever sees the content, mirroring how ChatGPT / Gemini hide their reasoning.
# ---------------------------------------------------------------------------
_THINK_PATTERN = re.compile(
    r"""(<think>[\s\S]*?</think>)         # <think>...</think> XML blocks
      | (<thinking>[\s\S]*?</thinking>)   # <thinking>...</thinking>
      | (<scratchpad>[\s\S]*?</scratchpad>)  # <scratchpad>...</scratchpad>
      | (<tool_call>[\s\S]*?</tool_call>) # stray tool_call leakage
      | (<function=[\w_]+>[\s\S]*?</function>)  # <function=…> XML
    """,
    re.VERBOSE,
)

_LEADING_THINK_PROSE = re.compile(
    r"""^[\s\S]*?
        (?:                                 # common internal-monologue markers
            Let\s+me\s+(?:re-)?(?:read|parse|analyze|look|trace|try|think|solve|check)\b
          | (?:Looking|Analyzing|Examining)\s+at\s+(?:the|this|S-\d)\b
          | Actually,?\s+(?:let|looking|I\s+need|I\s+think|wait)\b
          | (?:Wait|Hmm|OK),?\s
          | (?:Now\s+I\s+see|I\s+see|I\s+need)
          | Alright,?\s
          | The\s+user\s+(?:wants|is\s+asking|asked|provided)
          | (?:So\s+)?I\s+should\s+(?:look|check|analyze|solve|first)
        )
        [\s\S]+?\n{2,}                     # skip until a blank line
    """,
    re.VERBOSE,
)


def _scrub_thinking(raw: str) -> str:
    """Remove internal reasoning blocks that should never reach the user."""
    if not raw:
        return raw
    # 1. Remove XML-delimited thinking blocks
    cleaned = _THINK_PATTERN.sub("", raw)
    # 2. If the response still starts with chain-of-thought prose
    #    (English internal monologue before the actual answer), strip up to
    #    the first blank line that follows a recognisable CoT sentence.
    #    We only do this when the content starts with known CoT preambles.
    cot_starters = (
        "Let me ", "Looking at ", "Analyzing ", "Wait,", "Wait!", "Hmm,",
        "Actually,", "OK,", "Alright,", "The user ", "Now, ", "I need to ",
        "I'll ", "I see ", "I think ", "So I ",
    )
    stripped = cleaned.lstrip()
    if any(stripped.startswith(s) for s in cot_starters):
        # Walk through paragraphs and drop all leading CoT paragraphs
        paragraphs = re.split(r"\n{2,}", stripped)
        kept = []
        dropping = True
        for para in paragraphs:
            first_line = para.strip().split("\n")[0]
            if dropping:
                # Keep paragraph if it looks like actual structured output
                if (
                    first_line.startswith("#")           # markdown heading
                    or first_line.startswith("**S-")     # S-1, S-2 etc.
                    or first_line.startswith("**Çözüm")
                    or first_line.startswith("**Sonuç")
                    or re.match(r"^(S-\d|Soru\s*\d)", first_line)
                    or re.match(r"^\d+[.)]\s", first_line)   # numbered list
                    or first_line.startswith("---")
                    or first_line.startswith("===")
                    or "$$" in first_line                 # LaTeX
                    or "\\(" in first_line
                    or "\\[" in first_line
                    # stopped dropping once > 50 chars without CoT starter
                    or (len(para.strip()) > 50 and not any(first_line.startswith(s) for s in cot_starters))
                ):
                    dropping = False
                    kept.append(para)
            else:
                kept.append(para)
        if kept:
            cleaned = "\n\n".join(kept)
    return cleaned.strip()

AVAILABLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Searches the live internet for active job listings, internships, real-time facts, current news, official docs, or verified links. Call this whenever answering requires up-to-date or specific external web data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Targeted search keywords (in English or Turkish) optimized to find relevant listings or pages."
                    }
                },
                "required": ["query"]
            }
        }
    }
]

def _clean_url(href: str) -> str:
    if not href:
        return ""
    if "uddg=" in href:
        try:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            if "uddg" in parsed:
                return parsed["uddg"][0]
        except Exception:
            pass
    if href.startswith("//"):
        return "https:" + href
    return href

def _sync_web_search(query: str, max_results: int = 5) -> Tuple[str, List[Dict[str, str]]]:
    items: List[Dict[str, str]] = []
    
    # 1. Primary: Direct DDG HTML query (fast, bypasses bot challenges, returns direct job/career URLs)
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=6
        )
        if resp.status_code == 200:
            tree = html.fromstring(resp.content)
            for r in tree.xpath('//div[contains(@class, "result__body")]'):
                title_elem = r.xpath('.//h2/a')
                snippet_elem = r.xpath('.//a[contains(@class, "result__snippet")]')
                if title_elem:
                    title = "".join(title_elem[0].xpath('.//text()')).strip()
                    href = _clean_url(title_elem[0].get("href", ""))
                    snippet = "".join(snippet_elem[0].xpath('.//text()')).strip() if snippet_elem else ""
                    if title and href and not href.startswith("https://duckduckgo.com"):
                        items.append({"title": title, "link": href, "snippet": snippet})
                        if len(items) >= max_results:
                            break
    except Exception as e:
        print(f"[_sync_web_search HTML search error for '{query}']: {e}")

    # 2. Fallback to ddgs library if HTML returned no results
    if not items:
        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results))
                for r in raw:
                    t = r.get("title", "").strip()
                    l = _clean_url(r.get("href", "").strip())
                    s = r.get("body", "").strip()
                    if t and l:
                        items.append({"title": t, "link": l, "snippet": s})
        except Exception as e:
            print(f"[_sync_web_search ddgs fallback error for '{query}']: {e}")

    if not items:
        return "Canlı arama sonucu bulunamadı.", []

    lines = []
    for it in items:
        lines.append(f"- **Başlık**: {it['title']}\n  **Link**: {it['link']}\n  **Özet**: {it['snippet']}")
    return "\n\n".join(lines), items

class NvidiaProvider(BaseProvider):
    provider_name: str = "NVIDIA NIM"

    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None

    def _get_client(self) -> Optional[AsyncOpenAI]:
        """Lazy-initialize and reuse AsyncOpenAI client instance for NVIDIA NIM"""
        api_key = settings.NVIDIA_API_KEY.strip()
        if not api_key:
            return None
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=settings.NVIDIA_BASE_URL,
                api_key=api_key,
                timeout=settings.DEFAULT_TIMEOUT_SECONDS
            )
        return self._client

    def reload_client(self):
        self._client = None

    async def check_health(self) -> bool:
        return bool(settings.NVIDIA_API_KEY.strip())

    async def generate(
        self,
        model_id: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        timeout: float = 45.0,
        attachments: Optional[list] = None
    ) -> ProviderResponse:
        model_info = model_registry.get(model_id)
        model_name = model_info.name if model_info else model_id
        provider_name = model_info.provider if model_info else self.provider_name

        client = self._get_client()
        if client is None:
            return ProviderResponse(
                model_id=model_id,
                model_name=model_name,
                provider=provider_name,
                response_text=None,
                response_time_ms=None,
                status="error",
                error_message="NVIDIA API Key not configured. Please set NVIDIA_API_KEY in backend/.env."
            )

        # Process attachments
        doc_parts = []
        image_attachments = []
        if attachments:
            for item in attachments:
                # Support Pydantic model or dict
                ftype = getattr(item, "file_type", None) or (item.get("file_type") if isinstance(item, dict) else "")
                fname = getattr(item, "filename", None) or (item.get("filename") if isinstance(item, dict) else "Attachment")
                text = getattr(item, "extracted_text", None) or (item.get("extracted_text") if isinstance(item, dict) else "")
                durl = getattr(item, "data_url", None) or (item.get("data_url") if isinstance(item, dict) else "")
                pimages = getattr(item, "page_images", None) or (item.get("page_images") if isinstance(item, dict) else None)

                if ftype == "image" and durl:
                    image_attachments.append({"filename": fname, "data_url": durl})
                
                # If PDF has rendered page images, add them for vision models
                if pimages:
                    for p_idx, p_url in enumerate(pimages):
                        image_attachments.append({"filename": f"{fname} Sayfa {p_idx+1}", "data_url": p_url})

                if text:
                    doc_parts.append(f"📄 **[Ekli Belge: {fname}]**\n```\n{text[:35000]}\n```")

        doc_context = ("\n\n".join(doc_parts) + "\n\n---\n") if doc_parts else ""
        effective_prompt = f"{doc_context}{prompt}" if doc_context else prompt

        # Determine if this model is vision-capable
        is_vision = "vl" in model_id.lower() or "vision" in model_id.lower() or (
            model_info is not None and any(t in ["vision", "multimodal"] for t in model_info.tags)
        )

        # If user attached visual images / circuit drawings, but this model cannot see them,
        # return a clear, immediate notice rather than letting the text model hallucinate or apologize.
        if image_attachments and not is_vision:
            img_names = ", ".join([img["filename"] for img in image_attachments[:2]])
            return ProviderResponse(
                model_id=model_id,
                model_name=model_name,
                provider=provider_name,
                response_text=(
                    f"⚠️ **Görsel / Şema Uyarısı ({model_name})**\n\n"
                    f"Bu model **saf metin ve kod** odaklıdır; yüklediğiniz görsel veya devre şemasını ({img_names}) doğrudan algılayamaz. Tahmin yürüterek uydurma devre formülleri üretmesi ve yanıltıcı değerler vermesi engellenmiştir.\n\n"
                    f"👉 **Doğru Çözüm İçin:** Devre şemalarını ve çizimleri piksel düzeyinde inceleyen **Nemotron Super VL 120B** veya **Llama 3.2 90B Vision** modelini kullanınız."
                ),
                response_time_ms=5.0,
                status="success",
                error_message=None
            )

        messages = []
        effective_sys = system_prompt

        # Build a strong system prompt for vision+homework tasks
        if image_attachments and is_vision:
            vision_guidance = (
                "Sen uzman bir mühendis asistanısın. "
                "Kullanıcının yüklediği görseldeki devre şemalarını, diyagramları ve ödev sorularını piksel düzeyinde analiz et. "
                "ASLA İngilizce iç monolog, 'Let me re-read...', 'Actually...', 'Wait...' gibi karalama cümlesi yazma. "
                "Doğrudan kullanıcıya hitap et: her soru için (S-1, S-2, S-3, S-4) "
                "devre okuma → kullanılan yasa/formül → adım adım hesap → kutucuklu sayısal sonuç "
                "şeklinde Türkçe ve tertemiz bir ödev çözümü sun."
            )
            effective_sys = f"{effective_sys}\n\n{vision_guidance}" if effective_sys else vision_guidance

        if effective_sys:
            messages.append({"role": "system", "content": effective_sys})

        if image_attachments and is_vision:
            content_blocks = [{"type": "text", "text": effective_prompt}]
            for img in image_attachments[:5]:  # send up to 5 visual diagrams/pages
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": img["data_url"]}
                })
            messages.append({"role": "user", "content": content_blocks})
        else:
            messages.append({"role": "user", "content": effective_prompt})

        # Generous limits for multi-problem visual exam, deep reasoning, and long tables
        effective_max_tokens = 8192
        # Nemotron Super VL 120B uses a "thinking" budget on top of output tokens.
        # When content=None but reasoning_content is populated it means the model
        # used all tokens on thinking (finish_reason='length'). We bump the budget
        # to 16384 for vision models so there is room for BOTH thinking AND the
        # actual answer text.
        if is_vision or image_attachments:
            effective_max_tokens = 16384
        effective_timeout = max(timeout or 90.0, 150.0)

        start_time = time.perf_counter()

        try:
            # Autonomous Tool Capability: all models autonomously decide when to call web_search
            tools_param = AVAILABLE_TOOLS
            tool_choice_param = "auto"

            completion_coroutine = client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=0.7,
                max_tokens=effective_max_tokens,
                timeout=effective_timeout,
                tools=tools_param,
                tool_choice=tool_choice_param
            )
            completion = await asyncio.wait_for(completion_coroutine, timeout=effective_timeout)
            
            msg = completion.choices[0].message

            # Autonomous Tool Execution (ReAct Loop)
            collected_search_items: List[Dict[str, str]] = []
            if msg.tool_calls:
                messages.append(msg)
                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    if fn_name == "web_search":
                        try:
                            args = json.loads(tc.function.arguments)
                            search_q = args.get("query", "")
                        except Exception:
                            search_q = str(tc.function.arguments)

                        search_obs, items = await asyncio.to_thread(_sync_web_search, search_q, 6)
                        if items:
                            collected_search_items.extend(items)

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"[Canlı Arama Sonuçları - '{search_q}']:\n{search_obs}"
                        })

                # Follow-up directive to ensure synthesis into grounded markdown response
                messages.append({
                    "role": "user",
                    "content": "Canlı arama tamamlandı. Lütfen yukarıdaki arama sonuçlarında yer alan çalışan doğrudan bağlantıları ([Başlık](URL) biçiminde) ve gerçek pozisyonları kullanarak kullanıcının sorusuna doğrudan, eksiksiz ve profesyonel Türkçe nihai cevabını yaz."
                })

                # Second turn: Generate final response grounded in live web findings
                second_coroutine = client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=effective_max_tokens,
                    timeout=effective_timeout
                )
                second_completion = await asyncio.wait_for(second_coroutine, timeout=effective_timeout)
                final_msg = second_completion.choices[0].message
                content = final_msg.content or getattr(final_msg, "reasoning_content", "") or ""
            elif msg.content and "<tool_call>" in msg.content:
                # Some models (like Nemotron text variants) occasionally format tool calls directly as XML in message content
                qm = re.search(r"<parameter=query>([\s\S]*?)</parameter>", msg.content)
                if not qm:
                    qm = re.search(r'query["\']?\s*:\s*["\']([^"\']+)["\']', msg.content)
                search_q = qm.group(1).strip() if qm else prompt[:60]

                search_obs, items = await asyncio.to_thread(_sync_web_search, search_q, 6)
                if items:
                    collected_search_items.extend(items)

                messages.append({"role": "assistant", "content": f"Arama yapılıyor: {search_q}"})
                messages.append({
                    "role": "user",
                    "content": f"[Canlı Arama Sonuçları - '{search_q}']:\n{search_obs}\n\nCanlı arama tamamlandı. Lütfen yukarıdaki arama sonuçlarında yer alan çalışan doğrudan bağlantıları ([Başlık](URL) biçiminde) ve gerçek pozisyonları kullanarak kullanıcının sorusuna doğrudan, eksiksiz ve profesyonel Türkçe nihai cevabını yaz."
                })

                second_coroutine = client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=effective_max_tokens,
                    timeout=effective_timeout
                )
                second_completion = await asyncio.wait_for(second_coroutine, timeout=effective_timeout)
                final_msg = second_completion.choices[0].message
                content = final_msg.content or getattr(final_msg, "reasoning_content", "") or ""
            else:
                # --- PRIMARY FIX ---
                # Nemotron Super VL 120B (and similar reasoning models) place their
                # chain-of-thought in `reasoning_content` and the final answer in
                # `content`.  When finish_reason=='length' (token budget exhausted
                # during the thinking phase), `content` is None while
                # `reasoning_content` holds a half-finished internal monologue.
                # In that case we do a SECOND call without tools so the model just
                # writes its answer directly, skipping the thinking phase.
                raw_content = msg.content
                raw_reasoning = getattr(msg, "reasoning_content", None)

                if raw_content:
                    content = raw_content
                elif raw_reasoning:
                    # Token budget hit during thinking. Try again with higher limit
                    # and explicit instruction to output the answer, not thoughts.
                    retry_messages = messages + [
                        {
                            "role": "assistant",
                            "content": "Analiz tamamlandı. Aşağıda doğrudan Türkçe cevabı sunuyorum:"
                        }
                    ]
                    retry_coroutine = client.chat.completions.create(
                        model=model_id,
                        messages=retry_messages,
                        temperature=0.3,
                        max_tokens=effective_max_tokens,
                        timeout=effective_timeout
                    )
                    retry_completion = await asyncio.wait_for(retry_coroutine, timeout=effective_timeout)
                    retry_msg = retry_completion.choices[0].message
                    content = retry_msg.content or getattr(retry_msg, "reasoning_content", "") or ""
                else:
                    content = ""

            # ----------------------------------------------------------------
            # Final scrubbing pipeline — mirrors how ChatGPT/Gemini/Claude hide
            # their internal reasoning before presenting the user-facing answer.
            # ----------------------------------------------------------------
            # 1. Strip XML-delimited thinking blocks & stray tool tags
            content = _scrub_thinking(content)
            # 2. Remove any residual tool-call XML fragments
            for pat in [
                r"<tool_call>[\s\S]*?</tool_call>",
                r"<tool_call>", r"</tool_call>",
                r"<function=[\w_]+>", r"</function>",
                r"<parameter=[\w_]+>", r"</parameter>",
            ]:
                content = re.sub(pat, "", content)
            content = content.strip()

            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

            return ProviderResponse(
                model_id=model_id,
                model_name=model_name,
                provider=provider_name,
                response_text=content,
                response_time_ms=elapsed_ms,
                status="success",
                error_message=None,
                search_results=collected_search_items if collected_search_items else None
            )

        except asyncio.TimeoutError:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ProviderResponse(
                model_id=model_id,
                model_name=model_name,
                provider=provider_name,
                response_text=None,
                response_time_ms=elapsed_ms,
                status="timeout",
                error_message=f"Model response timed out after {effective_timeout} seconds."
            )

        except AuthenticationError:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ProviderResponse(
                model_id=model_id,
                model_name=model_name,
                provider=provider_name,
                response_text=None,
                response_time_ms=elapsed_ms,
                status="error",
                error_message="Authentication failed: Invalid or expired NVIDIA API Key."
            )

        except RateLimitError as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ProviderResponse(
                model_id=model_id,
                model_name=model_name,
                provider=provider_name,
                response_text=None,
                response_time_ms=elapsed_ms,
                status="error",
                error_message=f"Rate limit exceeded on NVIDIA NIM: {str(e)}"
            )

        except APIError as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ProviderResponse(
                model_id=model_id,
                model_name=model_name,
                provider=provider_name,
                response_text=None,
                response_time_ms=elapsed_ms,
                status="error",
                error_message=f"NVIDIA API Error: {str(e)}"
            )

        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ProviderResponse(
                model_id=model_id,
                model_name=model_name,
                provider=provider_name,
                response_text=None,
                response_time_ms=elapsed_ms,
                status="error",
                error_message=f"Unexpected error: {str(e)}"
            )

nvidia_provider = NvidiaProvider()
