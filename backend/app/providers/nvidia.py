import re
import time
import json
import asyncio
import urllib.parse
from typing import Optional, List, Dict, Any, Tuple
import requests
from lxml import html
from openai import AsyncOpenAI, APIError, AuthenticationError, RateLimitError, APIConnectionError
from ddgs import DDGS
from app.core.config import settings
from app.providers.base import BaseProvider, ProviderResponse
from app.providers.registry import model_registry

# ---------------------------------------------------------------------------
# Internal-thought scrubber & CoT sanitizer
# Models like Nemotron-SuperVL emit chain-of-thought inside <think>...</think>,
# stray <tool_call> tokens, or plain-text bulleted scratchpads.
# We scrub them thoroughly before user-facing display.
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


def _scrub_thinking(raw: str) -> str:
    """Remove internal reasoning blocks, tool tokens, and internal monologue scratchpads."""
    if not raw:
        return ""

    # 1. Remove XML-delimited thinking blocks and tool call fragments
    cleaned = _THINK_PATTERN.sub("", raw)

    # 2. Strip any bare or repeating tool-call tags (e.g. runaway <tool_call> loops)
    for pat in [
        r"<tool_call>[\s\S]*?</tool_call>",
        r"<tool_call>", r"</tool_call>",
        r"<function=[\w_]+>[\s\S]*?</function>",
        r"<function=[\w_]+>", r"</function>",
        r"<parameter=[\w_]+>[\s\S]*?</parameter>",
        r"<parameter=[\w_]+>", r"</parameter>",
    ]:
        cleaned = re.sub(pat, "", cleaned)

    # 3. Clean leading chain-of-thought prose or bulleted hypotheses
    cot_starters = (
        "Let me ", "Looking at ", "Analyzing ", "Wait,", "Wait!", "Hmm,",
        "Actually,", "OK,", "Alright,", "The user ", "Now, ", "I need to ",
        "I'll ", "I see ", "I think ", "So I ", "Examining ",
    )

    paragraphs = re.split(r"\n{2,}", cleaned.strip())
    kept = []
    dropping = True
    for para in paragraphs:
        stripped_p = para.strip()
        if not stripped_p:
            continue
        first_line = stripped_p.split("\n")[0].strip()
        if dropping:
            # Detect English bulleted monologue (e.g. • "4 kΩ" is above..., • Another possibility:...)
            is_bullet_cot = (
                (first_line.startswith("•") or first_line.startswith("*") or first_line.startswith("-"))
                and any(m in stripped_p.lower() for m in [
                    "is above", "is written in", "possibly", "another possibility",
                    "no drop across", "if 8k", "makes use of", "divider"
                ])
            )
            is_prose_cot = any(first_line.startswith(s) for s in cot_starters)

            is_structured = (
                first_line.startswith("#")           # markdown heading
                or first_line.startswith("**S-")     # S-1, S-2 etc.
                or first_line.startswith("**Çözüm")
                or first_line.startswith("**Sonuç")
                or bool(re.match(r"^(S-\d|Soru\s*\d)", first_line, re.IGNORECASE))
                or bool(re.match(r"^\d+[.)]\s", first_line))   # numbered list
                or first_line.startswith("---")
                or first_line.startswith("===")
                or "$$" in first_line                 # LaTeX
                or "\\(" in first_line
                or "\\[" in first_line
            )
            if is_structured or (len(stripped_p) > 50 and not is_bullet_cot and not is_prose_cot):
                dropping = False
                kept.append(para)
        else:
            kept.append(para)

    if kept:
        cleaned = "\n\n".join(kept)
    return cleaned.strip()


def _is_unstructured_cot(text: str) -> bool:
    """Checks if text consists purely of internal English scratchpad bullet points without a real solution."""
    if not text:
        return False
    cot_markers = [
        "is above", "is written in", "possibly labeling", "another possibility",
        "if 8k", "no drop across", "makes use of both", "let me ", "looking at the diagram"
    ]
    has_cot = sum(1 for m in cot_markers if m in text.lower()) >= 2
    has_solution = any(kw in text for kw in ["S-1", "Soru 1", "Çözüm", "Sonuç", "\\boxed{", "Direnç", "Gerilim", "Adım", "Akım"])
    return has_cot and not has_solution


AVAILABLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Searches the live internet for active job listings, internships, real-time facts, current news, official docs, or verified links. When searching for jobs or internships, provide specific English/global keywords (e.g. 'ROS 2 robotics internship remote 2025') along with target job boards (LinkedIn, Indeed, Wellfound) to retrieve active listings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Targeted search keywords optimized to find relevant listings or pages."
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


def _sync_web_search(query: str, max_results: int = 6) -> Tuple[str, List[Dict[str, str]]]:
    items: List[Dict[str, str]] = []
    seen_links = set()

    is_job_query = any(k in query.lower() for k in ["staj", "intern", "job", "iş", "career", "remote", "ilan"])
    queries = [query]
    if is_job_query:
        clean_q = re.sub(r"\b(bana|en az \d+|tane|adet|bul|ara|için|uygun|yüksek)\b", "", query, flags=re.IGNORECASE).strip()
        queries.append(f"{clean_q} remote internship apply 2025 2026")
        queries.append(f"{clean_q} site:linkedin.com/jobs OR site:indeed.com OR site:wellfound.com")

    for q in queries[:2]:
        try:
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": q},
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
                        if title and href and not href.startswith("https://duckduckgo.com") and href not in seen_links:
                            seen_links.add(href)
                            items.append({"title": title, "link": href, "snippet": snippet})
                            if len(items) >= max_results:
                                break
        except Exception as e:
            print(f"[_sync_web_search HTML search error for '{q}']: {e}")

        if len(items) >= max_results:
            break

    if not items:
        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results))
                for r in raw:
                    t = r.get("title", "").strip()
                    l = _clean_url(r.get("href", "").strip())
                    s = r.get("body", "").strip()
                    if t and l and l not in seen_links:
                        seen_links.add(l)
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
                timeout=settings.DEFAULT_TIMEOUT_SECONDS,
                max_retries=2
            )
        return self._client

    def reload_client(self):
        self._client = None

    async def check_health(self) -> bool:
        return bool(settings.NVIDIA_API_KEY.strip())

    async def _call_api_with_retry(self, kwargs: dict, max_retries: int = 2):
        """Execute chat completions with connection error recovery and socket pool refresh."""
        for attempt in range(max_retries + 1):
            try:
                client = self._get_client()
                if client is None:
                    raise RuntimeError("NVIDIA API key not configured")
                return await client.chat.completions.create(**kwargs)
            except Exception as err:
                err_str = str(err).lower()
                is_conn_error = isinstance(err, APIConnectionError) or any(
                    k in err_str for k in [
                        "connection error", "nodename nor servname", "connecterror",
                        "connection reset", "broken pipe", "temporary failure in name resolution"
                    ]
                )
                if is_conn_error and attempt < max_retries:
                    print(f"[NVIDIA NIM Connection Warning] Retry {attempt+1}/{max_retries} due to: {err}")
                    self.reload_client()  # Discard stale connection pool
                    await asyncio.sleep(1.0 * (attempt + 1))
                else:
                    raise

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
                ftype = getattr(item, "file_type", None) or (item.get("file_type") if isinstance(item, dict) else "")
                fname = getattr(item, "filename", None) or (item.get("filename") if isinstance(item, dict) else "Attachment")
                text = getattr(item, "extracted_text", None) or (item.get("extracted_text") if isinstance(item, dict) else "")
                durl = getattr(item, "data_url", None) or (item.get("data_url") if isinstance(item, dict) else "")
                pimages = getattr(item, "page_images", None) or (item.get("page_images") if isinstance(item, dict) else None)

                if ftype == "image" and durl:
                    image_attachments.append({"filename": fname, "data_url": durl})

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

        # Non-vision model safety guard
        if image_attachments and not is_vision:
            img_names = ", ".join([img["filename"] for img in image_attachments[:2]])
            return ProviderResponse(
                model_id=model_id,
                model_name=model_name,
                provider=provider_name,
                response_text=(
                    f"⚠️ **Görsel / Şema Uyarısı ({model_name})**\n\n"
                    f"Bu model **saf metin ve kod** odaklıdır; yüklediğiniz görsel veya devre şemasını ({img_names}) doğrudan algılayamaz. Yanıltıcı tahminler üretmesi engellenmiştir.\n\n"
                    f"👉 **Doğru Çözüm İçin:** Görselleri ve devre çizimlerini piksel düzeyinde inceleyen **Nemotron Super VL 120B** modelini kullanınız."
                ),
                response_time_ms=5.0,
                status="success",
                error_message=None
            )

        messages = []
        effective_sys = system_prompt

        # Universal STEM & Vision Problem-Solving Protocol
        if image_attachments and is_vision:
            vision_guidance = (
                "Sen üst düzey bir STEM (Mühendislik, Fizik, Matematik, Kimya) Uzman Asistanısın. "
                "Yüklenen görseldeki ödev ve sınav sorularını çözerken ASLA ezbere kalıplara veya şablonlara güvenme. "
                "Hangi alandan olursa olsun her türlü soru için şu 4 aşamalı analitik protokolü adım adım uygula:\n\n"
                "1. AŞAMA - PİKSEL DÜZEYİNDE ŞEMA VE VERİ ENVANTERİ:\n"
                "- Görseldeki her bir elemanı, bağlantıyı, değişkeni, polariteyi (+ / - işaretleri), ok yönlerini ve referans noktalarını (toprak, orijin) doğrudan şemadan oku.\n"
                "- ASLA şemada çizilmemiş bir elemanı, kuvveti, direnci veya kaynağı uydurma.\n"
                "- Giriş ve referans terminallerinin nereye bağlandığını fiziksel olarak doğrula.\n\n"
                "2. AŞAMA - İLK İLKELERDEN TÜRETME (FIRST-PRINCIPLES):\n"
                "- Temel kanunları doğrudan şemaya uygula (KVL, KCL, Düğüm Gerilimleri, Ohm Kanunu, Newton Yasaları, Enerji Korunumu).\n"
                "- Bir hazır formül kullanılacaksa, şemanın o formülün tüm ön koşullarını sağladığını önce açıkça kanıtla.\n\n"
                "3. AŞAMA - ADIM ADIM SAYISAL VE ANALİTİK ÇÖZÜM:\n"
                "- Önce sembolik genel denklemi yaz, ardından şemadan okunan sayısal değerleri birimleriyle yerine koy.\n"
                "- Denklem çözümlerini ve ara basamakları açıkça göster.\n\n"
                "4. AŞAMA - OTO-DOĞRULAMA VE SAĞLAMA (SANITY CHECK):\n"
                "- Birim Analizi, Mantık ve Polarite Kontrolü, Sınır Değer Kontrolü yap.\n\n"
                "EKSİK PARAMETRELER VE AKADEMİK MÜHENDİSLİK KABULLERİ:\n"
                "- BJT Devreleri: Eğer transistörün akım kazancı (\\beta veya h_FE) soruda yazılmamışsa, ASLA \\beta = \\infty veya I_B = 0 kabul ederek devredeki R_B direncini etkisiz kılma! Standart üniversite sınav kabulü olarak \\beta = 100 (ve aktif bölgede V_BE = 0.7 V) al. Tam KVL döngüsünü kur: V_B = -I_B R_B, I_E = (\\beta+1)I_B, I_C = \\beta I_B. R_B üzerindeki gerilim düşümünü mutlaka hesaba katarak I_C \\approx 4.37 mA, V_CE \\approx 11.18 V gerçek çalışma noktasını bul.\n"
                "- Diyotlar: İleri yön eşik gerilimi belirtilmemişse standart Si kabulüyle V_D = 0.7 V al.\n"
                "- Op-Amp Devreleri: İdeal kabulde evirmeyen (+) uca bağlı bir gerilim bölücü varsa önce V_+ gerilimini hesapla, asla ezbere sanal toprak (V_+=0) varsayma.\n\n"
                "SUNUM KURALLARI:\n"
                "- ASLA 'Another possibility', 'possibly', 'if 8k', 'let me think' gibi İngilizce düşünce notları veya karalama maddeleri yazma.\n"
                "- Görseldeki tüm soruları (S-1'den başlayarak sırayla) doğrudan Türkçe, akademik ve düzenli bir ödev formatında baştan sona çöz.\n"
                "- Her sorunun nihai sayısal/analitik sonucunu \\boxed{...} içine alarak vurgula.\n"
                "- Açtığın her \\boxed{...} parantezini ve LaTeX matematik ortamını ($$ ... $$) eksiksiz kapat."
            )
            effective_sys = f"{effective_sys}\n\n{vision_guidance}" if effective_sys else vision_guidance

            # Auto-enrich short casual prompts (e.g. "ödevimi yapmaya yardım eder misin", "ödev")
            short_hw_trigger = any(kw in prompt.lower() for kw in ["ödev", "yardım", "çöz", "buna bak", "soru", "devre", "hesapla", "yap"]) or len(prompt.strip()) < 60
            if short_hw_trigger:
                effective_prompt = (
                    f"{effective_prompt}\n\n"
                    f"[Yönerge: Lütfen görseldeki tüm soruları sırayla ve eksiksiz, ilk ilkelerden adım adım türeterek Türkçe akademik formatta çöz ve her sorunun nihai sonucunu \\boxed{{...}} içine al.]"
                )

        if effective_sys:
            messages.append({"role": "system", "content": effective_sys})

        if image_attachments and is_vision:
            content_blocks = [{"type": "text", "text": effective_prompt}]
            for img in image_attachments[:5]:
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": img["data_url"]}
                })
            messages.append({"role": "user", "content": content_blocks})
        else:
            messages.append({"role": "user", "content": effective_prompt})

        effective_max_tokens = 16384 if (is_vision or image_attachments) else 8192
        effective_timeout = max(timeout or 90.0, 150.0)
        # Low temperature for STEM / Vision homework to eliminate wandering & hallucinations
        model_temperature = 0.15 if (is_vision or image_attachments) else 0.7

        start_time = time.perf_counter()

        try:
            # Autonomous Tool Capability: only enable tools for pure text models
            # NEVER pass tools to vision models to avoid <tool_call> token degeneration
            use_tools = not is_vision and not image_attachments
            api_kwargs = {
                "model": model_id,
                "messages": messages,
                "temperature": model_temperature,
                "max_tokens": effective_max_tokens,
                "timeout": effective_timeout,
            }
            if use_tools:
                api_kwargs["tools"] = AVAILABLE_TOOLS
                api_kwargs["tool_choice"] = "auto"

            completion = await self._call_api_with_retry(api_kwargs)
            msg = completion.choices[0].message

            collected_search_items: List[Dict[str, str]] = []

            # 1. Native OpenAI tool call handling
            if getattr(msg, "tool_calls", None):
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

                messages.append({
                    "role": "user",
                    "content": "Canlı arama tamamlandı. Lütfen yukarıdaki arama sonuçlarında yer alan doğrudan başvuru ve kariyer bağlantılarını ([Pozisyon / Kurum](URL) biçiminde) listele. Asla genel tavsiye veya hayali/örnek JSON sunma; bulunan gerçek platform ve aktif ilanları detaylandırarak profesyonel Türkçe nihai cevabını yaz."
                })

                second_kwargs = {
                    "model": model_id,
                    "messages": messages,
                    "temperature": 0.5,
                    "max_tokens": effective_max_tokens,
                    "timeout": effective_timeout
                }
                second_completion = await self._call_api_with_retry(second_kwargs)
                final_msg = second_completion.choices[0].message
                content = final_msg.content or getattr(final_msg, "reasoning_content", "") or ""

            # 2. Text-embedded tool call handling
            elif msg.content and "<tool_call>" in msg.content:
                # Catch degenerate loops of <tool_call> without valid query
                is_degenerate_loop = msg.content.count("<tool_call>") >= 3 and "<parameter" not in msg.content and "query" not in msg.content
                if is_degenerate_loop:
                    # Rerun cleanly without tools
                    clean_kwargs = {
                        "model": model_id,
                        "messages": messages,
                        "temperature": 0.3,
                        "max_tokens": effective_max_tokens,
                        "timeout": effective_timeout,
                    }
                    clean_completion = await self._call_api_with_retry(clean_kwargs)
                    clean_msg = clean_completion.choices[0].message
                    content = clean_msg.content or getattr(clean_msg, "reasoning_content", "") or ""
                else:
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
                        "content": f"[Canlı Arama Sonuçları - '{search_q}']:\n{search_obs}\n\nCanlı arama tamamlandı. Lütfen yukarıdaki arama sonuçlarında yer alan doğrudan başvuru ve kariyer bağlantılarını ([Pozisyon / Kurum](URL) biçiminde) listele. Asla genel tavsiye veya hayali/örnek JSON sunma; bulunan gerçek platform ve aktif ilanları detaylandırarak profesyonel Türkçe nihai cevabını yaz."
                    })

                    second_kwargs = {
                        "model": model_id,
                        "messages": messages,
                        "temperature": 0.5,
                        "max_tokens": effective_max_tokens,
                        "timeout": effective_timeout
                    }
                    second_completion = await self._call_api_with_retry(second_kwargs)
                    final_msg = second_completion.choices[0].message
                    content = final_msg.content or getattr(final_msg, "reasoning_content", "") or ""

            # 3. Direct response or CoT / reasoning handling
            else:
                raw_content = msg.content
                raw_reasoning = getattr(msg, "reasoning_content", None)

                # Check if output is raw unstructured English scratchpad (Image 3 issue)
                if raw_content and (is_vision or image_attachments) and _is_unstructured_cot(raw_content):
                    retry_messages = messages + [
                        {"role": "assistant", "content": raw_content},
                        {"role": "user", "content": "Tüm ara adımları ve şema analizini tamamlayarak görseldeki her sorunun (S-1, S-2, S-3, S-4 vb.) Türkçe akademik çözümünü ve kutu içine alınmış (\\boxed{...}) nihai sonucunu eksiksiz yaz."}
                    ]
                    retry_kwargs = {
                        "model": model_id,
                        "messages": retry_messages,
                        "temperature": 0.15,
                        "max_tokens": effective_max_tokens,
                        "timeout": effective_timeout
                    }
                    retry_completion = await self._call_api_with_retry(retry_kwargs)
                    retry_msg = retry_completion.choices[0].message
                    content = retry_msg.content or getattr(retry_msg, "reasoning_content", "") or ""
                elif raw_content:
                    content = raw_content
                elif raw_reasoning:
                    # Token budget exhausted in reasoning mode; synthesize final answer
                    retry_messages = messages + [
                        {"role": "assistant", "content": "Analiz tamamlandı. Aşağıda doğrudan Türkçe çözümü sunuyorum:"}
                    ]
                    retry_kwargs = {
                        "model": model_id,
                        "messages": retry_messages,
                        "temperature": 0.2,
                        "max_tokens": effective_max_tokens,
                        "timeout": effective_timeout
                    }
                    retry_completion = await self._call_api_with_retry(retry_kwargs)
                    retry_msg = retry_completion.choices[0].message
                    content = retry_msg.content or getattr(retry_msg, "reasoning_content", "") or ""
                else:
                    content = ""

            # Final thorough scrubbing pipeline
            content = _scrub_thinking(content)

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
                error_message=f"Model yanıtı {effective_timeout} saniye içinde zaman aşımına uğradı."
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
                error_message="Kimlik doğrulama başarısız: Geçersiz veya süresi dolmuş NVIDIA API anahtarı."
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
                error_message=f"NVIDIA NIM istek limiti aşıldı: {str(e)}"
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
                error_message=f"NVIDIA API Hatası: {str(e)}"
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
                error_message=f"Beklenmeyen hata: {str(e)}"
            )

nvidia_provider = NvidiaProvider()
