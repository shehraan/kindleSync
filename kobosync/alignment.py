"""Find a unique narrated passage using contextual word timestamps."""
import math
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    # ASR may add possessive apostrophes or use a different apostrophe glyph.
    # Normalize within words without changing confidence or ambiguity checks.
    text = re.sub(r"(?<=[^\W_])['’](?=[^\W_])", "", text)
    return re.findall(r"[^\W_]+", text)


def match_passage(text: str, words: list[dict]) -> dict:
    query = tokens(text)[:40]
    if len(query) < 8:
        raise ValueError("Not enough context to match narration (at least eight words required)")
    spoken, origins = [], []
    for w in words:
        start, end = float(w["start"]), float(w["end"])
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start <= end:
            raise ValueError("Invalid transcript timestamps")
        for token in tokens(w["word"]):
            spoken.append(token)
            origins.append(w)
    index = defaultdict(list)
    for j in range(len(spoken)-2):
        index[tuple(spoken[j:j+3])].append(j)
    candidates = set()
    for i in range(len(query)-2):
        for j in index.get(tuple(query[i:i+3]), ()):
            for delta in (-1, 0, 1):
                if j-i+delta >= 0:
                    candidates.add(j-i+delta)
    ranked = []
    for start in candidates:
        window = spoken[start:start+len(query)]
        score = SequenceMatcher(None, query, window, autojunk=False).ratio()
        # A strong overall match cannot justify skipping an unmatched opening.
        if query[:3] == window[:3]:
            ranked.append((score, start))
    ranked.sort(reverse=True)
    if not ranked or ranked[0][0] < .88:
        raise ValueError("No confident narration match; check the edition or transcribe with a larger model")
    score, start = ranked[0]
    alternatives = [(s, p) for s, p in ranked[1:] if abs(p-start) > 4]
    if alternatives and alternatives[0][0] >= score-.08:
        raise ValueError("Narration match is ambiguous; a repeated passage needs more precise context")
    origin = origins[start]
    return dict(track=int(origin["track"]), seconds=float(origin["start"]),
                score=round(score, 4), passage=text[:600], precision="matched_saved_location")
