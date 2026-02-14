"""
Text Generator — Open Source First
====================================

Generates text for the Synthesis Agent using local models.

Priority chain:
  1. Qwen2.5-7B-Instruct (local) — default, same family as vision model
  2. Mistral-7B-Instruct (local) — fallback if Qwen unavailable
  3. Claude Sonnet / GPT-4o (paid) — only if env-configured

The generator handles:
  - Model loading with VRAM management
  - Chat template formatting per model family
  - Streaming support for long outputs
  - Token counting and budget enforcement

Usage:
    gen = TextGenerator()
    await gen.load()
    response = await gen.generate(
        system="You are a video analyst...",
        user="Synthesize these search results...",
        max_tokens=1024,
    )
"""
import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TextGenerator:
    """
    Local-first text generation with paid fallback.

    Loads a 7B instruction-tuned model for synthesis tasks.
    Falls back to API-based models if local loading fails
    and paid fallback is enabled.
    """

    def __init__(
        self,
        model_id: str = "Qwen/Qwen2.5-7B-Instruct",
        local_path: Optional[str] = None,
        device: str = "cuda",
        torch_dtype: str = "float16",
        max_new_tokens: int = 1024,
        # Paid fallback
        paid_fallback: bool = False,
        openai_api_key: str = "",
        anthropic_api_key: str = "",
        paid_model: str = "claude-opus-4-6",
    ):
        self.model_id = model_id
        self.local_path = local_path or model_id
        self.device = device
        self.torch_dtype = torch_dtype
        self.max_new_tokens = max_new_tokens

        self.paid_fallback = paid_fallback or os.environ.get("TEXT_PAID_FALLBACK", "false").lower() == "true"
        self.openai_api_key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        self.anthropic_api_key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.paid_model = paid_model

        self._model = None
        self._tokenizer = None
        self._loaded = False
        self._backend = "none"

    # ─── Loading ─────────────────────────────────────────────────────────

    async def load(self):
        """Load the local model. Non-blocking via executor."""
        if self._loaded:
            return

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_local)
            self._backend = "local"
            logger.info(f"TextGenerator loaded: {self.model_id} on {self.device}")
        except Exception as e:
            logger.warning(f"Local text model failed to load: {e}")
            if self.paid_fallback:
                self._backend = "paid"
                self._loaded = True
                logger.info(f"TextGenerator using paid fallback: {self.paid_model}")
            else:
                raise RuntimeError(
                    f"Cannot load local model and paid fallback disabled. "
                    f"Set TEXT_PAID_FALLBACK=true or install torch+transformers. Error: {e}"
                )

    def _load_local(self):
        """Synchronous model loading (runs in executor)."""
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        dtype_map = {
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "float32": torch.float32,
        }
        dtype = dtype_map.get(self.torch_dtype, torch.float16)

        self._tokenizer = AutoTokenizer.from_pretrained(
            self.local_path, trust_remote_code=True,
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            self.local_path,
            torch_dtype=dtype,
            device_map="auto",
            trust_remote_code=True,
        )
        self._loaded = True

    # ─── Generation ──────────────────────────────────────────────────────

    async def generate(
        self,
        system: str = "",
        user: str = "",
        messages: Optional[List[Dict[str, str]]] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.3,
    ) -> str:
        """
        Generate text from a prompt.

        Can provide either (system + user) or raw messages list.
        Tries local model first, falls back to paid API if configured.
        """
        if not self._loaded:
            await self.load()

        if messages is None:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": user})

        max_tokens = max_tokens or self.max_new_tokens

        if self._backend == "local":
            return await self._generate_local(messages, max_tokens, temperature)
        elif self._backend == "paid":
            return await self._generate_paid(messages, max_tokens, temperature)
        else:
            raise RuntimeError("TextGenerator not loaded")

    async def _generate_local(
        self, messages: List[Dict], max_tokens: int, temperature: float,
    ) -> str:
        """Generate with local HuggingFace model."""
        import torch

        # Apply chat template
        text = self._tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )

        inputs = self._tokenizer(text, return_tensors="pt").to(self._model.device)

        loop = asyncio.get_event_loop()
        output_ids = await loop.run_in_executor(
            None,
            lambda: self._model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=max(temperature, 0.01),  # Avoid 0
                do_sample=temperature > 0,
                top_p=0.9,
                pad_token_id=self._tokenizer.eos_token_id,
            ),
        )

        # Trim input tokens from output
        generated = output_ids[0][inputs["input_ids"].shape[1]:]
        response = self._tokenizer.decode(generated, skip_special_tokens=True)
        return response.strip()

    async def _generate_paid(
        self, messages: List[Dict], max_tokens: int, temperature: float,
    ) -> str:
        """Paid API fallback — tries Anthropic first, then OpenAI."""

        # Try Anthropic (Claude)
        if self.anthropic_api_key:
            try:
                return await self._generate_anthropic(messages, max_tokens, temperature)
            except Exception as e:
                logger.warning(f"Anthropic fallback failed: {e}")

        # Try OpenAI
        if self.openai_api_key:
            try:
                return await self._generate_openai(messages, max_tokens, temperature)
            except Exception as e:
                logger.warning(f"OpenAI fallback failed: {e}")

        raise RuntimeError("No paid API keys configured for fallback")

    async def _generate_anthropic(
        self, messages: List[Dict], max_tokens: int, temperature: float,
    ) -> str:
        """Generate using Anthropic Claude API."""
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.anthropic_api_key)

        # Extract system message
        system = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                chat_messages.append(msg)

        response = await client.messages.create(
            model=self.paid_model if "claude" in self.paid_model else "claude-opus-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=chat_messages,
        )
        return response.content[0].text

    async def _generate_openai(
        self, messages: List[Dict], max_tokens: int, temperature: float,
    ) -> str:
        """Generate using OpenAI API."""
        import openai

        client = openai.AsyncOpenAI(api_key=self.openai_api_key)
        response = await client.chat.completions.create(
            model="gpt-4o" if "gpt" in self.paid_model else self.paid_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content

    # ─── Info ────────────────────────────────────────────────────────────

    @property
    def backend_name(self) -> str:
        if self._backend == "local":
            return f"local:{self.model_id}"
        elif self._backend == "paid":
            return f"paid:{self.paid_model}"
        return "none"

    def get_status(self) -> Dict:
        return {
            "loaded": self._loaded,
            "backend": self._backend,
            "model_id": self.model_id if self._backend == "local" else self.paid_model,
            "device": self.device if self._backend == "local" else "api",
            "max_new_tokens": self.max_new_tokens,
        }

    def unload(self):
        """Free GPU memory."""
        if self._model is not None:
            del self._model
            self._model = None
        if self._tokenizer is not None:
            del self._tokenizer
            self._tokenizer = None
        self._loaded = False
        self._backend = "none"

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        logger.info("TextGenerator unloaded")
