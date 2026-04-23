# Peanut Mouth Sprite Pack — Generation Prompts

Generate these **9 PNGs** and drop them in this folder. Filenames must
match EXACTLY so `rhubarb_lip_sync.py` can pick them up automatically.

## Canvas specs (all 9 sprites)

- **Resolution**: 256 × 256 px (can be larger; the pipeline scales)
- **Format**: PNG with alpha (transparent background, NOT white/green)
- **Positioning**: The mouth should sit at the same absolute pixel
  coordinates in every sprite so we can overlay without re-aligning
- **Canvas**: Same pose, same lighting, same head angle — ONLY the
  mouth shape differs between sprites
- **Background transparency**: Confirm alpha=0 outside the face (check
  with Preview / image viewer showing checkerboard pattern)

## Reference image

Use `assets/peanut_face/burnt_peanut_v1_speaking.png` as the base pose
reference. We want to regenerate the SAME character head in 9 mouth
poses while keeping everything else identical.

## Style keywords (include in every prompt)

> **posh british peanut character, 3D rendered cartoon, smooth matte
> shell, calm butler expression, single peanut body pose, front-facing,
> transparent background, PNG with alpha, studio lighting, consistent
> character design, Pixar-style shading**

## The 9 prompts

### 1 · `mouth_X_rest.png` — Rest / idle pose (no speech)

> [style keywords]. Peanut character with **mouth completely relaxed
> and neutral — a soft line, lips together but not pressed, as if
> mid-thought**. Eyes looking forward, calm expression. This is the
> default silent pose shown when not speaking.

### 2 · `mouth_A_closed.png` — Closed (M, B, P consonants)

> [style keywords]. Peanut character with **lips fully pressed
> together in a firm but relaxed closed mouth**, as if pronouncing
> "mmm" or "bumble". No teeth visible. Slight tension at the corners.

### 3 · `mouth_B_small.png` — Slightly open, teeth touching (EE, IH, EH)

> [style keywords]. Peanut character with **mouth slightly open in a
> narrow horizontal slit, upper and lower teeth just barely visible and
> almost touching**, as if pronouncing "eee" or "bit". Lips taut,
> corners pulled slightly back.

### 4 · `mouth_C_open.png` — Open mouth (EH, AE — "red", "cat")

> [style keywords]. Peanut character with **mouth open in a medium
> rounded shape, about 60% open, lower jaw slightly dropped, both rows
> of teeth visible**, as if pronouncing "red" or "cat". Relaxed corners.

### 5 · `mouth_D_wide.png` — Wide open mouth (AA, AO — "father", "thought")

> [style keywords]. Peanut character with **mouth wide open, jaw fully
> dropped, mouth forming a large oval shape, tongue flat at the
> bottom**, as if pronouncing "ahh" or "father". This is the most open
> pose. Used on surprised / shocked reactions.

### 6 · `mouth_E_round_soft.png` — Slightly rounded (AH, ER — "about", "bird")

> [style keywords]. Peanut character with **mouth in a soft oval, lips
> gently rounded but not puckered, about 40% open**, as if pronouncing
> "uhh" or "err". Intermediate between open and round — a neutral
> speaking shape.

### 7 · `mouth_F_round_tight.png` — Puckered tight (OH, UH, OO, W)

> [style keywords]. Peanut character with **mouth in a small tight
> circle, lips pursed and pushed forward as if whistling or blowing
> out a candle**, pronouncing "ooh" or "woo". Small opening, visibly
> rounded lips, prominent pucker.

### 8 · `mouth_G_teeth_on_lip.png` — Upper teeth on lower lip (F, V)

> [style keywords]. Peanut character with **upper row of teeth pressed
> gently onto the lower lip**, forming the "fff" or "vvv" sound. The
> lower lip is tucked slightly inward, upper teeth visible on top of
> it. Mouth is otherwise nearly closed.

### 9 · `mouth_H_tongue.png` — Tongue visible (L, TH)

> [style keywords]. Peanut character with **mouth slightly open, tip
> of the tongue visible touching the upper front teeth**, as if
> pronouncing "lll" or "the". Lips relaxed, tongue is the focal
> detail. A subtle, playful pose.

## QA checklist before saving

For each generated PNG, verify:

- [ ] **Background is transparent** (not white, not green — checkerboard
  visible in your image viewer)
- [ ] **Head position** is identical to the other 8 (within 2-3 px —
  overlay any two and they should align)
- [ ] **Lighting** matches across all 9 (same shadow direction, same
  highlight placement)
- [ ] **Eye expression** is identical across all 9 (the character
  shouldn't change emotion just because the mouth changed — emotion
  comes from the body frame, mouth is independent)
- [ ] **Resolution** is the same across all 9
- [ ] **File is saved as PNG** with alpha, not JPG

## When done

Drop the 9 files into `assets/peanut_mouth_sprites/` with the exact
filenames above. Then run the smoke test:

```powershell
PYTHONPATH=src python scripts/smoke_peanut_lip_sync.py `
  --audio "C:/tmp/live_rx_uk_v5/tts/reaction_012.mp3" `
  --body  "C:/tmp/live_rx_uk_v5/reaction_clips/reaction_012.mp4" `
  --out   "C:/tmp/lipsync_smoke.mp4"
```

If the smoke renders a Peanut whose mouth moves in sync with the
"VERY IMPRESSIVE" audio, Phase 1A is done.

## Kling generation tips

- Use the **same seed** across all 9 generations (locks character
  identity — Kling has a "consistent character" mode)
- Generate one reference (prompt 1 `mouth_X_rest`) first. Confirm you
  like the head design. Then use that head image as the init / reference
  for the other 8, only changing the mouth description.
- If Kling keeps changing the head shape between generations, fall
  back to Flux/Midjourney with an IP-Adapter or identity-lock mode.
- **Alternative**: generate just 3 key shapes (A closed, D wide open,
  F puckered) and use Photoshop / GIMP to manually edit the other 6
  by sliding the mouth control points. Faster and more consistent.
