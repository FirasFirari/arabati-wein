"""VIN validation that FLAGS and PRIORITISES, and never rewrites stored data.

Measured on this registry (private/research/R4_vin.md, 2026-11-04):

* Position 9 is the ISO 3779 check digit only when it is a digit (or X). A
  letter there means the VIN format has no check digit at all -- 516 of the
  787 "failing" VINs were just that, and must never be called wrong.
* Toyota (JT*, RKL Kuozui, MHF, 6T1), and Hyundai/Kia in the digit-at-9
  variant, apply the check digit outside North America: a failure there is a
  real misreading signal. Mitsubishi, Honda Thailand, Nissan JN1, Mercedes do
  not. Toyota Thailand (MR0) is mixed -- a weak signal.
* On OCR output the signal is strong: reads two models agree on pass 99%,
  single reads 92%, disagreements 56%, old Google Vision reads 75%.
* O, I and Q are illegal in every VIN position, so folding them (O->0, I->1,
  Q->0) in a 17-character token is always safe -- in a SEARCH KEY only. 45
  stored "MRO..." VINs are Toyota Thailand "MR0..." read with a letter O.
  Everything else (S/5, B/8, Z/2, G/6...) is legal both ways: flag, never fold.
"""
from __future__ import annotations

import re

_TRANSLIT = {**{str(d): d for d in range(10)},
             **dict(zip("ABCDEFGH", range(1, 9))), **dict(zip("JKLMN", range(1, 6))),
             "P": 7, "R": 9, **dict(zip("STUVWXYZ", range(2, 10)))}
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_VIN17 = re.compile(r"^[A-Z0-9]{17}$")

# Families by whether they use the check digit OUTSIDE North America (R4 table).
STRONG = ("JT", "RKL", "MHF", "6T1", "TMA", "KM8", "JN6", "KMH", "KMJ")
WEAK = ("MR0", "KNA", "MAL", "JAM", "MD2", "L")
NO_CHECK = ("WDB", "JE3", "MMB", "JN1", "MRH", "MRD", "KMB", "MPA", "JMY")


def search_key(token: str) -> str:
    """Fold O/I/Q in a 17-character token; anything else is returned as is."""
    t = (token or "").upper()
    if len(t) == 17 and re.fullmatch(r"[A-Z0-9]{17}", t):
        return t.replace("O", "0").replace("I", "1").replace("Q", "0")
    return t


def check_digit(vin: str) -> str | None:
    """The ISO 3779 check character for a 17-character VIN, or None."""
    v = (vin or "").upper()
    if not _VIN17.match(v) or any(c in v for c in "IOQ"):
        return None
    total = sum(_TRANSLIT[c] * w for c, w in zip(v, _WEIGHTS))
    r = total % 11
    return "X" if r == 10 else str(r)


def has_check_position(vin: str) -> bool:
    """Position 9 holds a check digit only when it is a digit or X."""
    v = (vin or "").upper()
    return len(v) == 17 and (v[8].isdigit() or v[8] == "X")


def check_ok(vin: str) -> bool | None:
    """True/False when the VIN carries a check digit; None when it cannot."""
    v = search_key(vin)
    if not has_check_position(v):
        return None
    cd = check_digit(v)
    return None if cd is None else cd == v[8]


def family(vin: str) -> str:
    """'strong' | 'weak' | 'none' | 'unknown' -- does this maker use the check digit?"""
    v = search_key(vin)
    if not has_check_position(v):
        return "none"
    if v.startswith(NO_CHECK):
        return "none"
    if v.startswith(STRONG):
        return "strong"
    if v.startswith(WEAK):
        return "weak"
    return "unknown"


def flags(vin: str) -> list[str]:
    """Human-readable doubts about a stored 17-character identifier. Never errors."""
    raw = (vin or "").upper()
    if len(raw) != 17 or not re.fullmatch(r"[A-Z0-9]{17}", raw):
        return []
    out = []
    if raw.isdigit():
        return ["17 digits and no letters: probably not a VIN"]
    if any(c in raw for c in "IOQ"):
        out.append("contains I/O/Q, which no VIN uses: likely 1/0 misread")
    ok, fam = check_ok(raw), family(raw)
    if ok is False and fam == "strong":
        out.append("check digit fails: likely misread")
    elif ok is False and fam == "weak":
        out.append("check digit fails (weak signal for this maker)")
    return out


def _token_risk(tok: str) -> int:
    ok, fam = check_ok(tok), family(tok)
    if ok is False and fam == "strong":
        return 0
    if any(c in tok for c in "IOQ"):
        return 1
    if ok is False and fam == "weak":
        return 2
    if ok is True:
        return 4
    return 3


def second_read_risk(text: str) -> int:
    """Priority for a scarce second read of a photo's transcription (0 = first).

    The riskiest 17-character token decides: 0 strong-family VIN failing its
    check digit, 1 contains I/O/Q, 2 weak-family failure, 3 no check digit or
    unknown family (or no 17-character token at all), 4 passes.
    """
    tokens = [t.upper() for t in re.findall(r"[A-Za-z0-9]{17}", text or "") if not t.isdigit()]
    return min((_token_risk(t) for t in tokens), default=3)
