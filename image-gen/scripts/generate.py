#!/usr/bin/env python3
"""Generate images via Fuel proxy using the image-gen role."""

import base64
import json
import os
import sys
import tempfile
import urllib.request
import urllib.error

FUEL_BASE_URL = os.environ.get(
    "FUEL_BASE_URL",
    "http://fuel-proxy.openclawrocks.svc.cluster.local:8080/v1"
)
FUEL_API_KEY = os.environ.get("FUEL_API_KEY", "")


def generate_image(prompt: str) -> str:
    """Send an image generation request to fuel-proxy and return the file path."""
    if not FUEL_API_KEY:
        print("Error: FUEL_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    url = f"{FUEL_BASE_URL}/chat/completions"

    payload = json.dumps({
        "model": "image-gen",
        "modalities": ["image", "text"],
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": 4096
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {FUEL_API_KEY}"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        print(f"Error: Fuel proxy returned {e.code}: {error_body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Could not reach Fuel proxy: {e.reason}", file=sys.stderr)
        sys.exit(1)

    # Extract image from response
    choices = body.get("choices", [])
    if not choices:
        print("Error: No choices in response.", file=sys.stderr)
        print(f"Response: {json.dumps(body, indent=2)}", file=sys.stderr)
        sys.exit(1)

    message = choices[0].get("message", {})
    content = message.get("content", "")

    image_data = None
    mime_type = "image/png"

    # OpenRouter returns images in a top-level "images" array on the message
    images = message.get("images") or []
    if not images:
        # Also check content_parts / parts / content-as-list
        images = message.get("content_parts") or message.get("parts") or []
        if isinstance(content, list):
            images = content

    for part in images:
        if isinstance(part, dict):
            # OpenAI format: {"type": "image_url", "image_url": {"url": "data:..."}}
            if part.get("type") == "image_url":
                data_url = part.get("image_url", {}).get("url", "")
                if data_url.startswith("data:"):
                    header, b64 = data_url.split(",", 1)
                    mime_type = header.split(":")[1].split(";")[0]
                    image_data = base64.b64decode(b64)
                    break

            # Gemini native format: {"inline_data": {"mime_type": ..., "data": ...}}
            inline = part.get("inline_data")
            if inline:
                mime_type = inline.get("mime_type", "image/png")
                image_data = base64.b64decode(inline["data"])
                break

    # Fallback: check if content string contains a base64 data URI
    if image_data is None and isinstance(content, str):
        import re
        match = re.search(r'data:(image/[^;]+);base64,([A-Za-z0-9+/=]+)', content)
        if match:
            mime_type = match.group(1)
            image_data = base64.b64decode(match.group(2))

    if image_data is None:
        print("Error: No image found in response.", file=sys.stderr)
        print(f"Content: {content[:500] if isinstance(content, str) else json.dumps(content, indent=2)[:500]}", file=sys.stderr)
        sys.exit(1)

    # Determine file extension
    ext_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    ext = ext_map.get(mime_type, ".png")

    # Save to temp file
    fd, path = tempfile.mkstemp(suffix=ext, prefix="fuel-img-")
    with os.fdopen(fd, "wb") as f:
        f.write(image_data)

    return path


def main():
    if len(sys.argv) < 2:
        print("Usage: generate.py <prompt>", file=sys.stderr)
        sys.exit(1)

    prompt = " ".join(sys.argv[1:])
    path = generate_image(prompt)
    print(f"MEDIA:{path}")


if __name__ == "__main__":
    main()
