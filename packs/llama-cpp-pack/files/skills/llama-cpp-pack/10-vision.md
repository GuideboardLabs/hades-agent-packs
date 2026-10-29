# Vision: serving a multimodal model next to a text model

## The shape of it

llama.cpp serves vision with two files: the language model and a **projector** (`--mmproj`), which
turns image patches into embeddings the model can read. The projector is not optional and not bundled;
a VL GGUF without its matching mmproj is a text model.

```bash
llama-server -m Qwen3VL-4B-Instruct-Q4_K_M.gguf \
  --mmproj mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf \
  --host 127.0.0.1 --port 8123 -c 4096 -ngl 99
```

Requests use the OpenAI multimodal shape, image as base64 data URL:

```json
{"model": "qwen3vl", "max_tokens": 500, "temperature": 0.2,
 "messages": [{"role": "user", "content": [
   {"type": "text", "text": "What is on screen?"},
   {"type": "image_url", "image_url": {"url": "data:image/png;base64,<...>"}}]}]}
```

## Sizes, and what a small card can afford

| Pair | Model | Projector | Total |
|---|---|---|---|
| Qwen3VL-4B Instruct | Q4_K_M 2.5 GB | mmproj Q8_0 0.45 GB (F16 0.8 GB) | ~3.0 GB |
| Qwen3VL-8B Instruct | Q4_K_M ~5.0 GB | mmproj Q8_0 ~0.9 GB | ~5.9 GB |
| MiniCPM-V 4.7 35B-A3B | Q4_K_M 21.1 GB | mmproj F16 1.1 GB | needs its own card |

On an 8 GB card that also runs a 19B text model at 7.3 GB, the 4B pair does not have room. Three
honest options, in order of preference:

1. **Run the projector on CPU: `--no-mmproj-offload`.** Field reports on a 12 GB card use this to get
   vision "for ~0 extra VRAM". Encoding a 768 px image on CPU costs seconds, not minutes, and the
   text model keeps the card.
2. **Trim the text model while vision is in use.** More `-ncmoe`, smaller `-c`. Costs text throughput.
3. **Put vision on another machine or a hosted OpenAI-compatible endpoint.**

## Resize before you encode

Encode cost scales with pixels, and a 4K screenshot is thousands of times more pixels than a legible
768 px wide version. Clip to 768 px wide and the text in a UI screenshot stays readable while the
encoder work drops by an order of magnitude.

```bash
convert shot.png -resize 768x /tmp/shot-small.png
```

If the client library does this for you, keep it. If it silently skips (missing ImageMagick, or a
non-PNG format it cannot read), you pay full-size encode on every request.

## Two architectures for the calling application

**Flatten to text at the boundary.** The client calls the vision model with one question, gets one
answer, and prepends it to the conversation (`[the person sent an image. What it shows: ...]`). The
reasoning model never sees pixels. Cheap, works with any text model, keeps one prompt cache. Loses
the ability to re-examine, crop, compare two images, or ask a second question about the same picture
without re-encoding it.

**Send pixels to a multimodal chat model.** The main model is the vision model. Better reasoning over
the image, costs VRAM for a large VL model, and the whole transcript carries image tokens whether or
not they are needed.

Hades uses the first. Hermes uses the second. Neither is wrong; know which you built.

## Verifying a vision pair before trusting it

```bash
# 1. Does the build support projectors at all?
llama-server --help | grep -c mmproj

# 2. Load the pair on a spare port and ask about a known image.
llama-server -m Qwen3VL-4B-Instruct-Q4_K_M.gguf \
  --mmproj mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf \
  --host 127.0.0.1 --port 8124 -c 4096 -ngl 0 --no-warmup &

python3 - <<'PY'
import base64, json, urllib.request
img = base64.b64encode(open("/tmp/shot-small.png","rb").read()).decode()
body = {"model":"qwen3vl","max_tokens":200,"messages":[{"role":"user","content":[
    {"type":"text","text":"Transcribe the largest text in this image."},
    {"type":"image_url","image_url":{"url":"data:image/png;base64,"+img}}]}]}
req = urllib.request.Request("http://127.0.0.1:8124/v1/chat/completions",
    data=json.dumps(body).encode(), headers={"Content-Type":"application/json"})
print(json.load(urllib.request.urlopen(req, timeout=600))["choices"][0]["message"]["content"])
PY
```

`-ngl 0` keeps the whole test off the GPU, which is the right move when another job owns the card. If
the answer is right on CPU, the pair is good and any slowness is a placement problem, not a model
problem.

## Pitfalls

- **A VL model file without its mmproj is not a vision model.** If `--mmproj` is missing, the server
  loads and silently answers from text only.
- **Health does not mean identity.** `/health` says something is listening. `/props` says which
  weights loaded. If two servers can share a port, check `/props` before trusting a result.
- **Projector precision is usually cheaper to cut than model precision**: mmproj Q8_0 against F16
  costs 0.4 GB less and little quality.
- **One image per request in most clients.** Comparing two images usually means two calls and a text
  diff, unless the client builds a multi-image message itself.
- **The description is the whole memory of the image.** If the calling agent flattens to text, an
  underspecified question permanently loses the detail: ask for text transcribed verbatim and for
  the layout described, not "what is this".
