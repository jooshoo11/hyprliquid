"""
Unified LLM Client & Routing Engine (src/utils/llm_client.py)

Provides resilient, low-latency client integrations for:
1. Groq LPU (Ultra-fast inference: llama-3.1-8b-instant, llama-3.3-70b-versatile)
2. Google AI Studio (Gemini 2.0 Flash with Google Search Grounding)

Features:
- Structured JSON output enforcement
- Automatic rate-limit retry with exponential backoff
- Safe fallbacks when API keys are unconfigured (graceful degradation)
- Token and latency tracking
"""

import os
import json
import time
import re
from typing import Dict, Any, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Optional imports with graceful availability detection
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    Groq = None
    GROQ_AVAILABLE = False

try:
    import instructor
    INSTRUCTOR_AVAILABLE = True
except ImportError:
    instructor = None
    INSTRUCTOR_AVAILABLE = False

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    types = None
    GENAI_AVAILABLE = False


def clean_json_text(text: str) -> str:
    """Strip markdown code fences and extraneous text around JSON."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    return cleaned.strip()


from src.utils.pixel_ai import pixel_ai


class UnifiedLLMClient:
    """
    Unified manager routing between Pixel 9 Onboard AI (local zero-token), Groq, and Google AI Studio.
    """

    def __init__(
        self,
        groq_api_key: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
    ):
        self.pixel_ai = pixel_ai
        self.groq_api_key = groq_api_key if groq_api_key is not None else os.getenv("GROQ_API_KEY")
        self.gemini_api_key = gemini_api_key if gemini_api_key is not None else os.getenv("GEMINI_API_KEY")

        # Initialize Groq client if key is present
        self.groq_client = None
        if GROQ_AVAILABLE and self.groq_api_key:
            try:
                self.groq_client = Groq(api_key=self.groq_api_key)
                if INSTRUCTOR_AVAILABLE:
                    try:
                        self.instructor_groq = instructor.from_groq(self.groq_client, mode=instructor.Mode.JSON)
                    except Exception:
                        self.instructor_groq = None
                else:
                    self.instructor_groq = None
            except Exception as e:
                print(f"[LLM Client] Groq initialization notice: {e}")
                self.instructor_groq = None
        else:
            self.instructor_groq = None

        # Initialize Google GenAI client if key is present
        self.genai_client = None
        if GENAI_AVAILABLE and self.gemini_api_key:
            try:
                self.genai_client = genai.Client(api_key=self.gemini_api_key)
            except Exception as e:
                print(f"[LLM Client] Google GenAI initialization notice: {e}")

    def is_pixel_ready(self) -> bool:
        """Pixel 9 onboard engine is always available (local neural or deterministic quant)."""
        return True

    def is_groq_ready(self) -> bool:
        """Check if Groq inference is configured and available."""
        return bool(self.groq_client and self.groq_api_key)

    def is_gemini_ready(self) -> bool:
        """Check if Google AI Studio inference is configured and available."""
        return bool(self.genai_client and self.gemini_api_key)

    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic status of configured LLM providers."""
        return {
            "pixel_onboard": {
                "ready": True,
                "device": "Google Pixel 9 (Tensor G4)",
                "token_cost": "$0.00 (Local Offline)",
                "mode": "ONBOARD_NEURAL_ENGINE" if self.pixel_ai.is_online() else "LOCAL_QUANT_ENGINE",
            },
            "groq": {
                "installed": GROQ_AVAILABLE,
                "configured": bool(self.groq_api_key),
                "ready": self.is_groq_ready(),
                "default_model": "openai/gpt-oss-20b",
            },
            "gemini": {
                "installed": GENAI_AVAILABLE,
                "configured": bool(self.gemini_api_key),
                "ready": self.is_gemini_ready(),
                "default_model": "gemini-3-flash-preview",
                "search_grounding": True,
            },
        }

    def audit_risk_locally(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Audit risk on Pixel 9 without requiring cloud tokens."""
        return self.pixel_ai.audit_sentinel_risk(snapshot)

    def query_groq_json(
        self,
        prompt: str,
        system_prompt: str,
        model: str = "openai/gpt-oss-20b",
        temperature: float = 0.1,
        max_tokens: int = 300,
        retries: int = 3,
    ) -> Optional[Dict[str, Any]]:
        """
        Fast structured JSON query via Groq LPU (<100ms response latency).
        """
        if not self.is_groq_ready():
            return None

        models_to_try = [model, "qwen/qwen3.8-27b", "openai/gpt-oss-120b"]

        for active_model in models_to_try:
            for attempt in range(retries):
                try:
                    t0 = time.time()
                    completion = self.groq_client.chat.completions.create(
                        model=active_model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        response_format={"type": "json_object"},
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    raw_content = completion.choices[0].message.content
                    parsed = json.loads(clean_json_text(raw_content))
                    parsed["_latency_ms"] = round((time.time() - t0) * 1000, 1)
                    parsed["_model"] = active_model
                    parsed["_provider"] = "groq"
                    return parsed
                except Exception as e:
                    if "404" in str(e):
                        # Model not available, try next model in list
                        break
                    wait_sec = 2 ** attempt
                    if "429" in str(e) and attempt < retries - 1:
                        time.sleep(wait_sec)
                        continue
                    if attempt == retries - 1 and active_model == models_to_try[-1]:
                        print(f"[LLM Client] Groq query error: {e}")
                        return None
        return None

    def query_groq_structured(
        self,
        prompt: str,
        response_model: Any,
        system_prompt: Optional[str] = None,
        model: str = "openai/gpt-oss-20b",
        temperature: float = 0.1,
    ) -> Optional[Any]:
        """
        Query Groq with strict Pydantic model validation via jxnl/instructor.
        Returns a validated instance of response_model with zero parsing errors.
        """
        if not self.instructor_groq:
            return None

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        for attempt in range(3):
            try:
                res = self.instructor_groq.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_model=response_model,
                    temperature=temperature,
                )
                return res
            except Exception as e:
                if attempt == 2:
                    print(f"[Instructor Groq] Query error: {e}")
                    return None
                time.sleep(1.0)
        return None

    def query_gemini_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: str = "gemini-3-flash-preview",
        use_search: bool = False,
        temperature: float = 0.2,
        retries: int = 3,
    ) -> Optional[Any]:
        """
        Analytical query via Google AI Studio with optional Google Search Grounding.
        """
        if not self.is_gemini_ready():
            return None

        tools = []
        if use_search and types is not None:
            tools.append(types.Tool(google_search=types.GoogleSearch()))

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"System Instructions:\n{system_prompt}\n\nTask:\n{prompt}"

        for attempt in range(retries):
            try:
                t0 = time.time()
                config_kwargs: Dict[str, Any] = {
                    "temperature": temperature,
                }
                if tools:
                    config_kwargs["tools"] = tools
                else:
                    # When not using search tools, strict json mime type guarantees clean json
                    config_kwargs["response_mime_type"] = "application/json"

                config = types.GenerateContentConfig(**config_kwargs) if types else None

                response = self.genai_client.models.generate_content(
                    model=model,
                    contents=full_prompt,
                    config=config,
                )
                raw_text = response.text or ""
                cleaned = clean_json_text(raw_text)
                parsed = json.loads(cleaned)
                meta_info = {
                    "_latency_ms": round((time.time() - t0) * 1000, 1),
                    "_model": model,
                    "_provider": "google_ai_studio",
                    "_grounded": use_search,
                }
                if isinstance(parsed, dict):
                    parsed.update(meta_info)
                return parsed
            except Exception as e:
                if tools and ("RESOURCE_EXHAUSTED" in str(e) or "429" in str(e)):
                    # Search tool quota exhausted, retry immediately without search tool
                    tools = []
                    continue
                wait_sec = 2 ** attempt
                if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and attempt < retries - 1:
                    time.sleep(wait_sec)
                    continue
                print(f"[LLM Client] Gemini query error (attempt {attempt + 1}/{retries}): {e}")
                if attempt == retries - 1:
                    return None
        return None
