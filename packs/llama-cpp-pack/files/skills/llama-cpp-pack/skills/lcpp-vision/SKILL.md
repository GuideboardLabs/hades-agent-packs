---
name: lcpp-vision
description: "Serving a vision model (mmproj) next to a text model on one card."
version: 1.0.0
license: MIT
tags: [llama-cpp, vision, mmproj, multimodal]
---

# Vision on a shared card

llama.cpp needs two files: the VL model and its projector (`--mmproj`). A VL GGUF without the
projector is a text model that will not say so.

```bash
llama-server -m Qwen3VL-4B-Instruct-Q4_K_M.gguf \
  --mmproj mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf \
  --host 127.0.0.1 --port 8123 -c 4096 -ngl 0
```

## The placement decision

- **`-ngl 0` (CPU)**: costs zero VRAM, about 13 to 16 s per 768 px image on a 6-core Ryzen. The
  right default when a text model already owns the card.
- **`-ngl 99` (GPU)**: about a second per image, but a 4B Q4 pair wants ~3 GiB and an 8 GiB card
  running a 19B has ~0.5 GiB free.
- **`--no-mmproj-offload`**: field reports use this to keep the model on the GPU and push only the
  projector to the CPU, described as vision "for ~0 extra VRAM" on a 12 GB card.

Sizes: Qwen3-VL 4B Q4_K_M 2.33 GiB + mmproj Q8_0 0.42 GiB; Qwen3-VL 8B Q4 ~5 GiB + ~0.9 GiB.

## Verify before wiring a client

```bash
python3 vision-ask.py 8123 shot.png "Transcribe every word of text, verbatim."
```

`vision-ask.py` posts the OpenAI multimodal shape (`image_url` with a base64 data URL). If the
answer is right on CPU, the pair is good and any slowness is placement, not the model.

## Resize first

Encode cost scales with pixels. `convert shot.png -resize 768x out.png` keeps ordinary UI text
legible at a fraction of the work. A client library that silently skips this pays seconds per
request for nothing.

## Two client architectures

- **Flatten to text**: call the vision model with one question, prepend the answer to the chat
  history. Cheap, works with any text model, and the image is gone forever. Ask for text verbatim
  and for layout described, because you get one chance.
- **Pixels into the chat model**: better reasoning about the image, costs VRAM for a large VL model,
  and every turn carries image tokens.

Hades uses the first; Hermes uses the second. Know which one you are extending.
