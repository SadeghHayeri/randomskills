---
name: image-gen
description: Generate images via Fuel proxy. No API key needed on Pro plans.
metadata:
  openclaw:
    emoji: art
    requires:
      bins: ["python3"]
      env: ["BIFROST_VIRTUAL_KEY"]
    primaryEnv: BIFROST_VIRTUAL_KEY
---

# Image Generation

Generate images using the Fuel proxy.

## Usage

Run `scripts/generate.py` with a text prompt describing the image you want to create.

```bash
python3 scripts/generate.py "a sunset over the ocean with vibrant colors"
```

The script outputs a `MEDIA:<path>` line pointing to the generated image file.

## Requirements

- `python3` (bundled in the OpenClaw container)
- `BIFROST_VIRTUAL_KEY` environment variable (set automatically on Pro instances)

## How it works

1. Sends a chat completion request to the Fuel proxy with `model: "image-gen"` and `modalities: ["image", "text"]`
2. Fuel proxy routes to the configured image generation model
3. Parses the base64-encoded image from the response
4. Saves the image to a temporary file and prints the `MEDIA:` path
