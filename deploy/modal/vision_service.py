"""Modal deployment for a self-hosted cloud vision service.

An alternative to Replicate for when the per-call cost stops making
sense (rough break-even: ~50 describes/day = ~1500/month at
$0.003/call = $4.50/mo on Replicate. Modal with an A10G and ~15 min
of actual GPU time/day runs ~$5-8/mo with better cold-start control
and lower per-call latency).

Ships an OpenAI-compatible /v1/chat/completions endpoint that
clip_context.py can call as an Ollama replacement — just point
OLLAMA_URL at the Modal endpoint URL instead of localhost:11434.

Deployment:
    pip install modal
    modal token new
    modal deploy deploy/modal/vision_service.py

Modal prints the public URL after deploy (e.g. `https://yourname--peanut-vision-chat.modal.run`).
Then in GitHub Actions secrets:

    OLLAMA_URL            = https://yourname--peanut-vision-chat.modal.run
    OLLAMA_VISION_MODEL   = qwen2.5-vl-7b

clip_context.py's existing Ollama routing works without code change.

Cost model:
  - A10G GPU: $1.10/hr when active, $0 when idle (container_idle_timeout=60)
  - Cold start: ~20 s first call after idle; ~0.5 s while warm
  - 3 Shorts/day x 5 describes = 15 describes/day @ ~3 s each = 45 s active = ~$0.014/day = $0.42/mo
  - Even with a VPS pipeline also dialing in: < $3/mo total

NOTE: this file is a TEMPLATE. Until you run `modal deploy` nothing
happens — it's source code waiting to be deployed. Use Replicate
(already wired, zero deploy) for first-pass C2.
"""
from __future__ import annotations

import modal

# Modal stub / app definition
app = modal.App("peanut-vision")

# Container image: lean, transformers + torch + fastapi. No vLLM for v1
# to keep image simple; swap to vLLM later if throughput becomes a bottleneck.
VISION_IMAGE = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git")
    .pip_install(
        "transformers>=4.45.0",
        "accelerate>=0.33.0",
        "torch>=2.2.0",
        "Pillow>=10.0.0",
        "fastapi>=0.110.0",
        "pydantic>=2.0.0",
        "httpx>=0.27.0",
    )
)

# The model we serve. Qwen2.5-VL-7B because vLLM + transformers support
# is rock-solid in April 2026. Swap to Qwen3-VL-8B when the Transformers
# release catches up.
MODEL_NAME = "Qwen/Qwen2.5-VL-7B-Instruct"

# Secrets: Hugging Face token for model download. Create at
# https://huggingface.co/settings/tokens then: modal secret create hf-token HF_TOKEN=<your token>
HF_SECRET = modal.Secret.from_name("hf-token")


@app.cls(
    image=VISION_IMAGE,
    gpu="A10G",
    timeout=300,
    container_idle_timeout=60,   # Scale to zero after 60 s idle
    secrets=[HF_SECRET],
)
class VisionModel:
    """One model instance per Modal container. The @enter hook loads
    the model on container boot, not on every call."""

    @modal.enter()
    def setup(self):
        import os
        import torch
        from transformers import AutoModelForVision2Seq, AutoProcessor

        os.environ["HF_TOKEN"] = os.environ.get("HF_TOKEN", "")

        self.processor = AutoProcessor.from_pretrained(MODEL_NAME)
        self.model = AutoModelForVision2Seq.from_pretrained(
            MODEL_NAME,
            torch_dtype=torch.bfloat16,
            device_map="cuda",
            trust_remote_code=True,
        )
        self.model.eval()

    @modal.method()
    def describe(self, image_b64: str, prompt: str, max_tokens: int = 120) -> str:
        """Describe a single frame. Returns a short text description."""
        import base64
        import io
        import torch
        from PIL import Image

        # image_b64 can be "data:image/jpeg;base64,..." or raw base64
        if image_b64.startswith("data:"):
            image_b64 = image_b64.split(",", 1)[-1]
        img_bytes = base64.b64decode(image_b64)
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image"},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        text = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )
        inputs = self.processor(
            text=text, images=[image], return_tensors="pt",
        ).to("cuda")

        with torch.no_grad():
            output_ids = self.model.generate(
                **inputs, max_new_tokens=max_tokens, do_sample=False,
            )
        decoded = self.processor.batch_decode(
            output_ids[:, inputs["input_ids"].shape[1]:],
            skip_special_tokens=True,
        )[0]
        return decoded.strip().strip('"\'')


@app.function(image=VISION_IMAGE)
@modal.asgi_app(label="chat")
def openai_compat_app():
    """FastAPI wrapping the model as an OpenAI-compatible /v1/chat/completions
    endpoint + a /api/version stub so clip_context's Ollama health check
    passes unchanged."""
    from fastapi import FastAPI, HTTPException, Request

    api = FastAPI(title="peanut-vision", version="1.0")

    # Shared instance — Modal will spin up the GPU container on first call
    model = VisionModel()

    @api.get("/api/version")
    async def version():
        # Ollama returns something like {"version": "0.3.14"} here; our
        # reachability check in clip_context.py just wants a 200. Any JSON
        # works.
        return {"version": "peanut-vision-1.0", "backend": "modal", "model": MODEL_NAME}

    @api.post("/v1/chat/completions")
    async def chat_completions(req: Request):
        body = await req.json()

        # Extract the text prompt + single image out of the OpenAI-style
        # messages array. We only look at the first user message, which
        # is what clip_context.py sends.
        messages = body.get("messages", [])
        if not messages:
            raise HTTPException(status_code=400, detail="no messages")

        user_content = messages[0].get("content", [])
        prompt = ""
        frames: list[str] = []
        for part in user_content:
            if part.get("type") == "text":
                prompt = part.get("text", "")
            elif part.get("type") == "image_url":
                url = part.get("image_url", {}).get("url", "")
                if url:
                    frames.append(url)

        if not frames:
            raise HTTPException(status_code=400, detail="no images in message")

        # Middle-frame as-representative for a short clip. Matches the
        # Replicate tier's behavior for consistency.
        mid_frame = frames[len(frames) // 2]
        max_tokens = int(body.get("max_tokens", 120))
        description = model.describe.remote(mid_frame, prompt, max_tokens)

        return {
            "object": "chat.completion",
            "model": MODEL_NAME,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": description},
                    "finish_reason": "stop",
                }
            ],
        }

    return api


# Local test hook: `modal run deploy/modal/vision_service.py`
@app.local_entrypoint()
def main(image_url: str = "", prompt: str = "Describe this image in one sentence."):
    """Quick local test. Pass an image URL and we run it through the model."""
    import base64
    import httpx

    if not image_url:
        print("Usage: modal run deploy/modal/vision_service.py --image-url URL")
        return

    # Fetch the image, base64 it, call describe
    img = httpx.get(image_url, timeout=30).content
    b64 = base64.b64encode(img).decode("ascii")
    model = VisionModel()
    description = model.describe.remote(b64, prompt)
    print(f"Description: {description}")
