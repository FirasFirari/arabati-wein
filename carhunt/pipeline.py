"""Shared analysis entry point with the free cost gates applied.

Order matters — each gate avoids spend the next one would incur:
1. deterministic identifier scan   (free, and catches the case that matters)
2. relevance pre-filter            (free, skips non-vehicle posts entirely)
3. duplicate-content reuse         (free, reuses a verdict for a cross-post)
4. tiered model call               (ONLY when live scoring is switched on)

Live scoring is OFF by default since 2026-11-04 (the owner's call: $0). It
paid the Claude API for every relevant Telegram post (28 in the week before),
and those writes skipped the grounding gate. With it off, step 4 leaves the
post unscored and the free daily lane extracts it through the gated path.
Steps 1-3 still run instantly: the target's own identifiers are matched the
moment a post arrives, which is the only thing that can raise an alert.
"""
from . import db
from .prefilter import identifier_hit, is_relevant


def live_scoring(cfg: dict) -> bool:
    """config.yaml `live_scoring.enabled` (default false: no paid calls)."""
    return bool((cfg.get("live_scoring") or {}).get("enabled"))


def make_analyzer(cfg: dict):
    """The paid model client, or None when live scoring is off."""
    if not live_scoring(cfg):
        return None
    from .analyzer import Analyzer
    return Analyzer(cfg)


def analyze_post(analyzer, cfg: dict, post_id: int,
                 text: str | None, media_paths: list[str],
                 post_source: str | None = None) -> dict:
    """Score one stored post, spending API budget only when necessary."""
    car = cfg["car"]
    if post_source is None:
        stored = db.post_with_media(post_id)
        post_source = (stored or {}).get("source")

    # 1. our own identifiers, checked exactly and for free
    hit = identifier_hit(text, car)
    if hit:
        verdict = {
            "score": 95,
            "is_car_post": True,
            "model_match": True,
            "reasons": f"DETERMINISTIC IDENTIFIER MATCH: {hit} — found by exact "
                       f"text scan, not model inference. Verify immediately.",
            "tier": "identifier_scan",
        }
        db.save_analysis(post_id, verdict)
        return verdict

    # 2. posts with no vehicle content and no images cannot concern the car.
    #    General-interest sources are held to a stricter bar: their photos are
    #    news images, so the text must mention a vehicle too.
    source = (post_source or "").lower()
    strict = any(s.lower() in source
                 for s in (cfg.get("general_sources") or []))
    if not is_relevant(text, media_paths, strict=strict):
        verdict = {"score": 0, "is_car_post": False, "tier": "prefiltered",
                   "reasons": "No vehicle vocabulary, chassis-like number or image."}
        db.save_analysis(post_id, verdict)
        return verdict

    # 3. identical content already scored elsewhere (lists get cross-posted).
    #    TEXT-ONLY posts only: identical boilerplate captions are common while
    #    the attached photos differ, and reusing a verdict there would skip
    #    image analysis — exactly where the car would be recognised.
    chash = db.content_hash(text) if not media_paths else None
    if chash:
        db.set_content_hash(post_id, chash)
        prior = db.verdict_for_hash(chash, post_id)
        if prior:
            verdict = dict(prior)
            verdict["tier"] = "duplicate"
            verdict["vehicles_copied_from_duplicate"] = True
            verdict["reasons"] = (f"Identical to an already-analyzed post. "
                                  f"{verdict.get('reasons', '')}")[:600]
            db.save_analysis(post_id, verdict)
            return verdict

    # 4. pay for a model call -- only if live scoring is on
    if analyzer is None:
        return {"score": None, "tier": "queued",
                "reasons": "Stored. The target's identifiers were checked now; the "
                           "full reading runs in the free daily lane."}
    verdict = analyzer.analyze(text, media_paths)
    db.save_analysis(post_id, verdict)
    return verdict
