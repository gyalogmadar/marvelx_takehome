"""What a model call costs: USD per million tokens, per model.

A model is priced by its exact name, else by family: local servers report whatever
name the weights were loaded under (a path, a quantisation suffix, a fine-tune's
name), so a family pattern matches any name the server gives.
"""
from __future__ import annotations

import re

# USD per million tokens: (input, output, cache read)
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "scripted": (0.0, 0.0, 0.0),
}

# (pattern over the reported model name, lowercased; price) - the first match wins
FAMILIES: list[tuple[str, tuple[float, float, float]]] = [
    # Qwen 27B (Qwen3.6-27B-UD-Q4_K_XL.gguf, qwen3.8-27b-...): 262k context
    (r"qwen[\d.]*-27b", (0.25, 2.09, 0.04)),
]


def price_of(model: str) -> tuple[float, float, float]:
    if model in PRICES:
        return PRICES[model]
    name = model.rsplit("/", 1)[-1].lower()
    return next((price for pattern, price in FAMILIES if re.search(pattern, name)), (0.0, 0.0, 0.0))


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int) -> float:
    price_in, price_out, price_cache = price_of(model)
    return round((input_tokens - cache_read_tokens) * price_in / 1e6 + cache_read_tokens * price_cache / 1e6
                 + output_tokens * price_out / 1e6, 6)
