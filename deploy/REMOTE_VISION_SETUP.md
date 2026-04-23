# Remote Vision Setup (C2)

Enables clip-description vision calls from environments that don't
have a local GPU — primarily the GitHub Actions Shorts pipeline and
optionally the Hetzner VPS.

Our `clip_context.describe_clip()` routes through four tiers in order
and picks the first that works. Configure any of them via env vars;
the rest fall through automatically.

| Tier | Backend | Enabled by | Cost | Latency | Best for |
|---|---|---|---|---|---|
| 1 | Local Ollama | `OLLAMA_VISION_MODEL=qwen3-vl:8b` + server running | $0 | 2-4 s | Local dev, VPS with GPU |
| 2 | **Replicate** | `REPLICATE_API_TOKEN=r8_...` | ~$0.003/call | 3-6 s | **GitHub Actions, zero deploy** |
| 3 | Modal (self-deployed) | `OLLAMA_URL=https://yourname--...modal.run` | ~$0.001/call | 0.5-3 s warm | High-volume, lower per-call cost |
| 4 | Groq (free fallback) | `GROQ_API_KEY=gsk_...` | $0 (rate-limited) | 1-2 s | Fallback only |

## Quick start — Replicate (recommended for v1)

Use this unless you're already running Modal for something else.

### 1. Get a Replicate token

- Sign up at https://replicate.com — first $0 of credit included, pay-as-you-go after
- Account → API tokens → New token → copy the `r8_...` value

### 2. Add to your environments

**GitHub Actions** (for the Shorts workflow):

Repo Settings → Secrets and variables → Actions → New repository secret:
- Name: `REPLICATE_API_TOKEN`
- Value: your `r8_...` token

The `shorts-daily.yml` workflow already references this secret, so the
next run picks it up with no other changes.

**Hetzner VPS** (if you want cloud GPU even though the VPS itself has
no GPU — makes sense for the `peanut_tiktok_reacts` channel):

```bash
ssh root@46.224.219.236
echo "REPLICATE_API_TOKEN=r8_..." >> /opt/peanut-reacts/.env
systemctl restart peanut-scheduler.service
```

**Local dev** (if you want to test without Ollama):

```powershell
$env:REPLICATE_API_TOKEN = "r8_..."
```

### 3. Verify it's routing

Run any describe call (e.g. kick the Actions workflow manually). In the
logs you'll see one of:

- `[CTX] Clip described (ollama/qwen3-vl:8b): ...`  → local Ollama used
- `[CTX] Clip described (replicate/qwen2-vl-7b-instruct): ...` → **Replicate used** ✓
- `[CTX] Clip described (groq/meta-llama/...): ...` → fell through, something's wrong

### Cost estimate

- 3 Shorts/day × 5 describes each = 15/day = 450/month
- At $0.003/call = **$1.35/month**
- At max volume (3 channels × 3 crons × 10 describes × 30 days) = 2700 calls/month = $8.10/month

## Advanced — Modal (for lower per-call cost at volume)

If you grow to hundreds of describes/day, self-deploying on Modal beats
Replicate on cost. `deploy/modal/vision_service.py` is ready to deploy.

### One-time setup

```bash
pip install modal
modal token new                              # opens browser auth
modal secret create hf-token HF_TOKEN=hf_... # your HuggingFace token
modal deploy deploy/modal/vision_service.py
```

Modal prints a URL like `https://yourname--peanut-vision-chat.modal.run`.

### Configure the client

Point `OLLAMA_URL` at the Modal URL (not localhost). The existing Ollama
routing in `clip_context.py` works unchanged because `vision_service.py`
implements both `/v1/chat/completions` (OpenAI-style) AND `/api/version`
(Ollama-style health check).

```bash
OLLAMA_URL="https://yourname--peanut-vision-chat.modal.run"
OLLAMA_VISION_MODEL="qwen2.5-vl-7b"
```

For GitHub Actions: add both as repo secrets and extend the workflow's
env block.

### Model choice

`vision_service.py` ships with `Qwen/Qwen2.5-VL-7B-Instruct`. To swap:
edit `MODEL_NAME` at the top of `vision_service.py` and redeploy.
Qwen3-VL-8B will be the natural next upgrade once `transformers` ships
a stable release for it.

### Cost

- A10G @ $1.10/hr when active
- Container idles to zero after 60 s
- Realistic month: $0.50-3 at Shorts volume, $5-15 at production
  multi-channel volume
- Break-even vs Replicate: ~400 calls/day

## Fallback chain — proof

The code in `src/peanut_reacts/character/clip_context.py:describe_clip`
tries Ollama first, then Replicate, then Groq, then a generic string.
Each tier returns `None` on any failure so the next one is tried
automatically. No single backend being down takes out the pipeline.

To test the fallback works locally:

```powershell
$env:REPLICATE_API_TOKEN = "r8_FAKE_BAD_TOKEN"  # forces Replicate to 401
$env:GROQ_API_KEY = "gsk_..."                    # valid key
PYTHONPATH=src python -c "from peanut_reacts.character.clip_context import describe_clip; from pathlib import Path; print(describe_clip(Path('C:/tmp/live_rx_uk_v5/reaction_clips/reaction_005.mp4')))"
```

Expected: one line `Replicate returned 401`, then a successful Groq
response, then the describe is printed. That's the fallback chain
working.
