# 05 — Shopping by photo (拍照找货)

## What is this?

You see a product in the wild — on a shelf, in someone else's hands, in a
livestream screenshot. You don't know what it's called. You take a photo
and ask "do you have this?". Our app looks at the photo, finds the most
visually similar product in our catalog, and answers.

The trick: we use a neural network called **CLIP** that puts both images
AND text into the same mathematical space, so we can ask "which products
look like this photo?" the same way we ask "which products match this
query".

## Why does it matter?

Two common situations:

1. **The user doesn't have the right words.** "Black wireless headphones
   with the silver dot on the side" — that's a slow way to describe Sony
   WH-1000XM5. A photo cuts through.
2. **Cross-language shopping.** A user in Beijing sees a French brand on
   Instagram. They don't know the brand name in Chinese or English. A
   photo finds it.

This is bonus rubric item **4.2 (multimodal — 拍照找货)**.

## How we built it

### What CLIP does (in plain English)

A normal embedding model (like the one we use for text — see
[`02-finding-products.md`](02-finding-products.md)) turns sentences into
vectors. **CLIP** does the same trick, but it was trained on hundreds of
millions of (image, caption) pairs from the internet. The result: an image
of a dog and the text "a dog" end up close together in vector space. An
image of a sneaker and the text "shoe" likewise.

So CLIP gives us a shared 512-number coordinate system where pictures and
words live in the same neighborhood. Now we can:

- Turn each product's image into a 512-number vector at startup.
- When the user uploads a photo, turn that photo into a vector too.
- Find products whose vector is closest to the photo's vector.

That's the whole game.

### The specific model and where it runs

We use **OpenCLIP** with the `ViT-B/32` backbone — a small, fast,
publicly available CLIP variant. It runs on:

- **A100 GPU** (`ssh uc`) for the one-time ingest pass that builds
  vectors for all 145 products. Takes ~30 seconds.
- **Mac CPU** at request time when a user uploads a photo. Takes ~250 ms
  per photo (a Mac with M-series chip is fast enough).

Code: `rag/ingest/embed_image.py` (build product vectors at startup),
`rag/retrieve/query.py` (lookup at request time). The product vectors
get stored in a second Chroma collection called `products_image`
alongside the text collection (`rag/store.py`).

### How a request flows

```
User: taps + → 照片库 → picks a photo of sunscreen
        │
        ▼
[iOS] Downsamples photo to 1280px (saves upload bandwidth)
      base64-encodes it
      sends as part of /chat/stream payload
        │
        ▼
[Backend] Detects image_url in the request
          Decodes base64 → raw image bytes
          Calls OpenCLIP → 512-number vector
          Queries Chroma's products_image collection for top-3 matches
        │
        ▼
[Backend] Pass those 3 products into the regular LLM context (same as
          a text query). LLM reads: "the user uploaded an image; the
          visual match is product XYZ"
        │
        ▼
[LLM] Generates reply: "您拍的是 Sony WH-1000XM5 头戴降噪耳机, 售价 ¥2698.68 …"
        │
        ▼
User sees the recommendation + product card.
```

### Multi-image: what about uploading 3 photos at once?

We added support for up to 10 attachments in Round 8.E. When the user
uploads multiple images, we use the FIRST one for CLIP retrieval — the
visual model takes one image at a time — but we still pass ALL the images
to the vision-capable LLM (currently `claude-haiku-4-5` via TokenRouter).
The LLM can reason across all 10 images even though our retrieval only
saw one.

This is a deliberate split: visual retrieval is bounded (CLIP works best
on a single subject), but conversational reasoning isn't.

### Image downsampling on the iOS side

iPhone JPEGs are big — a 4032×3024 photo is around 2.4 MB. If we sent
that raw, the network upload would dominate the response time and the
LLM would chew through tokens on giant base64 strings. Before uploading,
we resize to 1280px on the longest edge and re-encode JPEG at quality
0.78. The same photo becomes ~120 KB — a 20× reduction with no visible
quality loss for AI-perceived content.

Code: `client/AAALionApp/AAALionApp/Models/Attachment.swift`, the
`compressForUpload` function.

### Honest limitation: CLIP isn't a Chinese model

OpenCLIP ViT-B/32 was trained mostly on English web data. It handles
generic categories (face wash / sunscreen / headphones) well. It's less
strong on specific Chinese brands or products that appear in Chinese
e-commerce but rarely in the OpenAI/LAION training data. Our catalog
is small enough (145 products) that this isn't a huge problem — we
manually re-checked the index — but a future improvement would be to
fine-tune CLIP on Chinese product photos.

## Where to dig deeper

- `rag/ingest/embed_image.py` — the script that builds image vectors at
  ingest time.
- `rag/retrieve/query.py::query_image` — the request-time image
  retrieval.
- `client/AAALionApp/AAALionApp/Models/Attachment.swift` — the iOS image
  pipeline (picker, downsample, encode).
- [`02-finding-products.md`](02-finding-products.md) — same retrieval
  infrastructure, but for text queries.
- `docs/HARDWARE.md` — A100 setup for the one-time CLIP ingest.
