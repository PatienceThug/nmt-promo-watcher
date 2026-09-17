import re
import watcher_v3 as w

# Extend the core watcher with the Turkish phrase "promosyon kodu" without
# duplicating the stable v3 implementation.
w.SEARCH_QUERIES = list(dict.fromkeys(w.SEARCH_QUERIES + [
    '"nmt.gg" "promosyon kodu"',
    '"NMT" "promosyon kodu"',
]))
w.YOUTUBE_QUERIES = list(dict.fromkeys(w.YOUTUBE_QUERIES + [
    "NMT.GG promosyon kodu",
]))

w.KEYWORDS = re.compile(
    r"(promo\s*code|promocode|promo\s*kod\w*|promokod\w*|promosyon\s*kod\w*|"
    r"промокод\w*|промо\s*код\w*|промо-код\w*|"
    r"bonus\s*code|gift\s*code|hediye\s*kod\w*|hediye\s*code|"
    r"free\s*case|ücretsiz\s*case|coupon\s*code|voucher\s*code)",
    re.I,
)

w.DIRECT_PATTERNS = [
    re.compile(
        r"(?:promo\s*code|promocode|promo\s*kod\w*|promokod\w*|promosyon\s*kod\w*|"
        r"промокод\w*|промо\s*код\w*|промо-код\w*|"
        r"bonus\s*code|gift\s*code|hediye\s*kod\w*|hediye\s*code)"
        r"\s*[:=/#\-–—]*\s*[`\"'“”‘’]*([A-Z0-9][A-Z0-9_\-]{4,31})",
        re.I,
    ),
    re.compile(
        r"([A-Z0-9][A-Z0-9_\-]{4,31})[`\"'“”‘’]*\s*"
        r"(?:promo\s*code|promocode|promokod|промокод|promo\s*kod\w*|promosyon\s*kod\w*|hediye\s*kod\w*)",
        re.I,
    ),
]

if __name__ == "__main__":
    w.main()
