"""
Vision Backend — Open Source First, Paid Configurable
=====================================================

Model hierarchy:
  1. Qwen2.5-VL-7B (default) — best open-source, temporal understanding,
     runs on single RTX 3070 in float16, native video input via HuggingFace
  2. Video-LLaVA-7B — HuggingFace native, good for unified image+video
  3. VideoLLaMA2-7B — strong spatial-temporal + audio understanding
  4. LLaVA-NeXT-Video — zero-shot video from image-trained model

Paid fallbacks (opt-in via env VISION_PAID_FALLBACK=true):
  - GPT-4o (OpenAI) — best quality, $0.005/frame
  - Gemini Pro Vision (Google) — long video support, competitive pricing

The abstraction layer means agents never touch model internals.
They call `analyze_video_segment()` and get structured output back.
"""
import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import VisionBackend, VisionConfig

logger = logging.getLogger(__name__)


class VisionAnalysis:
    """Structured output from video analysis."""

    def __init__(
        self,
        description: str,
        entities: List[str],
        topics: List[str],
        actions: List[str],
        claims: List[str],
        temporal_summary: str,
        raw_response: str = "",
        backend_used: str = "",
        frame_count: int = 0,
    ):
        self.description = description
        self.entities = entities
        self.topics = topics
        self.actions = actions          # Temporal: what happened over time
        self.claims = claims            # Verifiable factual claims
        self.temporal_summary = temporal_summary
        self.raw_response = raw_response
        self.backend_used = backend_used
        self.frame_count = frame_count

    def to_dict(self) -> Dict:
        return {
            "description": self.description,
            "entities": self.entities,
            "topics": self.topics,
            "actions": self.actions,
            "claims": self.claims,
            "temporal_summary": self.temporal_summary,
            "backend_used": self.backend_used,
            "frame_count": self.frame_count,
        }


# ─── Prompts ────────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """Analyze this video segment carefully. Provide a structured JSON response with these fields:

{
  "description": "A detailed description of what is shown and discussed in this video segment",
  "entities": ["List of specific named entities: people, organizations, products, locations, etc."],
  "topics": ["List of topics and subjects discussed or shown"],
  "actions": ["List of actions/events that happen over the course of this segment, in temporal order"],
  "claims": ["List of specific factual claims made that could be verified (statistics, dates, facts, quotes)"],
  "temporal_summary": "A narrative of what happens from the beginning to end of this segment"
}

Be specific. Extract real names, numbers, and facts. For claims, only include statements that assert specific facts (not opinions). Respond ONLY with valid JSON."""


# ─── Open Source Backend: Qwen2.5-VL ───────────────────────────────────

class Qwen25VLBackend:
    """
    Qwen2.5-VL-7B-Instruct — best open-source video understanding model.

    Features:
    - Native video input (not just frame concatenation)
    - Temporal understanding via M-RoPE with absolute time encoding
    - Dynamic frame rate training
    - 7B runs on single GPU with 16GB VRAM in float16
    - Supports 20+ minute videos
    """

    def __init__(self, config: VisionConfig):
        self.config = config
        self.model = None
        self.processor = None
        self._loaded = False

    async def load(self):
        if self._loaded:
            return
        logger.info(f"Loading {self.config.model_id} on {self.config.device}...")

        import torch
        from transformers import Qwen2VLForConditionalGeneration, AutoProcessor

        dtype_map = {
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "float32": torch.float32,
        }
        dtype = dtype_map.get(self.config.torch_dtype, torch.float16)

        self.model = Qwen2VLForConditionalGeneration.from_pretrained(
            self.config.model_id,
            torch_dtype=dtype,
            device_map="auto",
        )
        self.processor = AutoProcessor.from_pretrained(self.config.model_id)
        self._loaded = True
        logger.info(f"Loaded {self.config.model_id}")

    async def analyze(self, video_path: str, prompt: str = ANALYSIS_PROMPT) -> str:
        """Analyze video with Qwen2.5-VL native video understanding."""
        if not self._loaded:
            await self.load()

        from qwen_vl_utils import process_vision_info

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "video", "video": f"file://{video_path}"},
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        text = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self.processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        ).to(self.model.device)

        generated_ids = self.model.generate(**inputs, max_new_tokens=self.config.max_new_tokens)
        trimmed = [
            out[len(inp):] for inp, out in zip(inputs.input_ids, generated_ids)
        ]
        output = self.processor.batch_decode(trimmed, skip_special_tokens=True)[0]
        return output


# ─── Open Source Backend: Video-LLaVA ──────────────────────────────────

class VideoLLaVABackend:
    """
    Video-LLaVA-7B — unified image+video understanding via HuggingFace.
    Available directly in `transformers` library.
    """

    def __init__(self, config: VisionConfig):
        self.config = config
        self.model = None
        self.processor = None
        self._loaded = False

    async def load(self):
        if self._loaded:
            return
        logger.info("Loading Video-LLaVA-7B...")

        import torch
        from transformers import VideoLlavaForConditionalGeneration, VideoLlavaProcessor

        self.model = VideoLlavaForConditionalGeneration.from_pretrained(
            "LanguageBind/Video-LLaVA-7B-hf",
            torch_dtype=torch.float16,
            device_map="auto",
        )
        self.processor = VideoLlavaProcessor.from_pretrained(
            "LanguageBind/Video-LLaVA-7B-hf"
        )
        self._loaded = True
        logger.info("Loaded Video-LLaVA-7B")

    async def analyze(self, video_path: str, prompt: str = ANALYSIS_PROMPT) -> str:
        if not self._loaded:
            await self.load()

        import numpy as np
        from decord import VideoReader, cpu

        vr = VideoReader(video_path, ctx=cpu(0))
        total = len(vr)
        indices = np.linspace(0, total - 1, min(self.config.max_frames, total), dtype=int)
        frames = vr.get_batch(indices).asnumpy()

        inputs = self.processor(
            text=f"USER: <video>\n{prompt}\nASSISTANT:",
            videos=frames,
            return_tensors="pt",
        ).to(self.model.device)

        out = self.model.generate(**inputs, max_new_tokens=self.config.max_new_tokens)
        return self.processor.batch_decode(out, skip_special_tokens=True)[0]


# ─── Paid Fallback: GPT-4o ─────────────────────────────────────────────

class GPT4oBackend:
    """GPT-4o vision — paid fallback with best-in-class quality."""

    def __init__(self, config: VisionConfig):
        self.config = config

    async def analyze(self, video_path: str, prompt: str = ANALYSIS_PROMPT) -> str:
        import cv2
        import openai

        client = openai.AsyncOpenAI(api_key=self.config.openai_api_key)

        # Sample frames from video
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        step = max(1, total // self.config.max_frames)

        frames_b64 = []
        for i in range(0, total, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ret, frame = cap.read()
            if not ret:
                break
            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            frames_b64.append(base64.b64encode(buf).decode())
            if len(frames_b64) >= self.config.max_frames:
                break
        cap.release()

        # Build multi-image message
        content = [{"type": "text", "text": f"These are {len(frames_b64)} frames sampled from a video. {prompt}"}]
        for b64 in frames_b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
            })

        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            max_tokens=self.config.max_new_tokens,
        )
        return resp.choices[0].message.content


# ─── Unified Vision Interface ──────────────────────────────────────────

class VisionEngine:
    """
    Unified interface — agents call this, never touch models directly.

    Tries open-source backend first. If it fails and paid fallback is enabled,
    falls through to GPT-4o/Gemini.
    """

    def __init__(self, config: VisionConfig):
        self.config = config
        self._backends = self._init_backends()

    def _init_backends(self) -> List:
        """Build backend chain: open-source first, paid fallback last."""
        chain = []

        if self.config.backend == VisionBackend.QWEN25_VL:
            chain.append(("qwen2.5-vl", Qwen25VLBackend(self.config)))
        elif self.config.backend == VisionBackend.VIDEO_LLAVA:
            chain.append(("video-llava", VideoLLaVABackend(self.config)))
        # Add more open-source backends as needed

        if self.config.paid_fallback_enabled and self.config.openai_api_key:
            chain.append(("gpt-4o", GPT4oBackend(self.config)))

        return chain

    async def analyze_segment(
        self,
        video_path: str,
        prompt: str = ANALYSIS_PROMPT,
    ) -> VisionAnalysis:
        """
        Analyze a video segment. Tries open-source first, paid fallback if configured.
        Returns structured VisionAnalysis regardless of which backend succeeded.
        """
        raw_response = ""
        backend_used = ""

        for name, backend in self._backends:
            try:
                logger.info(f"Trying vision backend: {name}")
                if hasattr(backend, "load"):
                    await backend.load()
                raw_response = await backend.analyze(video_path, prompt)
                backend_used = name
                break
            except Exception as e:
                logger.warning(f"Backend {name} failed: {e}")
                continue

        if not raw_response:
            return VisionAnalysis(
                description="Vision analysis unavailable",
                entities=[], topics=[], actions=[], claims=[],
                temporal_summary="No vision backend available",
                backend_used="none",
            )

        # Parse structured output
        return self._parse_response(raw_response, backend_used)

    def _parse_response(self, raw: str, backend: str) -> VisionAnalysis:
        """Parse JSON response from any backend into VisionAnalysis."""
        try:
            # Try to extract JSON from response
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(raw[start:end])
                return VisionAnalysis(
                    description=data.get("description", ""),
                    entities=data.get("entities", []),
                    topics=data.get("topics", []),
                    actions=data.get("actions", []),
                    claims=data.get("claims", []),
                    temporal_summary=data.get("temporal_summary", ""),
                    raw_response=raw,
                    backend_used=backend,
                )
        except (json.JSONDecodeError, KeyError):
            pass

        # Fallback: treat entire response as description
        return VisionAnalysis(
            description=raw[:1000],
            entities=[], topics=[], actions=[], claims=[],
            temporal_summary=raw[:500],
            raw_response=raw,
            backend_used=backend,
        )
