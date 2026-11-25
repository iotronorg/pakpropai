"""Script and Roman Urdu detection for fixed (model-free) replies and the eval suites."""
from __future__ import annotations

import re

_ARABIC = re.compile(r'[\u0600-\u06FF]')
_URDU_ONLY = re.compile(r'[\u0679\u0688\u0691\u067E\u0686\u06AF\u06BA\u06BE\u06C1\u06CC\u06D2\u06A9]')
_ROMAN = re.compile(
    r'\b(?:hai|hain|kya|kaise|chahiye|chahta|chahti|mujhe|mujhay|mera|meri|aap|apna|agar|ghar|makaan|zameen|bechna|'
    r'khareedna|kharidna|kiraya|kiraye|mein|ka|ki|ke|ko|se|tak|aur|nahi|koi|dikhao|batao|karo|karein|salam)\b', re.I)


def detect(text: str) -> str:
    """'ur' (Urdu script), 'ar', 'roman' (Roman Urdu) or 'en'."""
    text = text or ''
    if _ARABIC.search(text):
        return 'ur' if _URDU_ONLY.search(text) else 'ar'
    return 'roman' if len(_ROMAN.findall(text)) >= 2 else 'en'
