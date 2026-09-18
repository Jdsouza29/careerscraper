"""Pull structured requirements out of a job description.

Rule-based on purpose: every number it reports comes with the snippet it came from,
so a wrong answer is visible instead of mysterious. No LLM, no API key.

Produces: min/max years of experience, required vs preferred skills, work mode,
education. Section headings decide required vs preferred; when a description has no
recognisable headings, everything counts as required.
"""

import re

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_NUM = r"\d{1,2}|" + "|".join(NUMBER_WORDS)

EXPERIENCE_RE = re.compile(
    rf"(?P<lo>{_NUM})\s*"
    rf"(?:(?P<plus>\+)|\s*(?:-|–|—|to)\s*(?P<hi>{_NUM})\s*\+?)?\s*"
    r"(?:years?|yrs?|y\.?o\.?e\.?)\b",
    re.IGNORECASE,
)

# A year count only means "required experience" near words like these.
EXPERIENCE_CONTEXT = re.compile(
    r"experien|exp\b|expertise|background|hands[- ]?on|professional|industry|"
    r"track record|yoe|working with|building|develop",
    re.IGNORECASE,
)
# ...and never when the sentence is about company history or the recent past.
EXPERIENCE_BLOCKER = re.compile(
    r"for (?:more than|over|nearly|almost)|past \d|last \d|\bago\b|founded|"
    r"history|anniversary|since \d{4}|over the (?:last|past)|in the (?:last|past)",
    re.IGNORECASE,
)

PREFERRED_HEADING = re.compile(
    r"nice[- ]to[- ]have|bonus|preferred|good[- ]to[- ]have|desirable|"
    r"advantage|plus(?:es)?\b|extra credit|icing|stand ?out",
    re.IGNORECASE,
)
REQUIRED_HEADING = re.compile(
    r"requirement|qualification|what you.{0,4}ll (?:need|bring|do|own)|"
    r"what we.{0,4}re looking for|who you are|about you|must[- ]have|"
    r"you.{0,4}ll (?:need|have|bring)|the role|responsibilit|"
    r"skills|experience|basic|minimum",
    re.IGNORECASE,
)

EDUCATION_RE = re.compile(
    r"\b(bachelor(?:'?s)?|master(?:'?s)?|btech|b\.tech|mtech|m\.tech|"
    r"mba|ph\.?d|doctorate|degree in)\b",
    re.IGNORECASE,
)
# Degree abbreviations are case-sensitive on purpose: a lowercase "be" is the verb.
EDUCATION_ABBREV_RE = re.compile(
    r"\b(B\.E\.?|M\.E\.?|B\.S\.?c?|M\.S\.?c?|BSc|MSc|BCA|MCA|"
    r"BE(?=\s*/)|ME(?=\s*/))\b"
)

REMOTE_RE = re.compile(r"fully remote|remote[- ]first|work from home|\bremote\b", re.IGNORECASE)
HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
ONSITE_RE = re.compile(r"on[- ]?site|in[- ]office|in the office|work from office", re.IGNORECASE)

MAX_PLAUSIBLE_YEARS = 40
HEADING_MAX_LEN = 90


def parse(job, config):
    """Return the structured requirement fields for one job."""
    text = job.get("description") or ""
    required_text, preferred_text = split_sections(text, job.get("sections"))

    min_years, max_years, snippet = parse_experience(required_text or text)
    if min_years is None and required_text:
        min_years, max_years, snippet = parse_experience(text)

    vocab = compile_skills(config.get("skills") or {})
    required_skills = match_skills(required_text or text, vocab)
    preferred_skills = [s for s in match_skills(preferred_text, vocab)
                        if s not in required_skills]

    return {
        "min_years_exp": min_years,
        "max_years_exp": max_years,
        "exp_snippet": snippet,
        "skills_required": ", ".join(required_skills),
        "skills_preferred": ", ".join(preferred_skills),
        "education": detect_education(required_text or text),
        "work_mode": detect_work_mode(job, text),
    }


def split_sections(text, sections=None):
    """Split a description into (required_text, preferred_text).

    Prefers adapter-supplied (heading, body) pairs - Lever and SmartRecruiters give
    those directly. Otherwise sniffs heading-looking lines out of the plain text.
    """
    buckets = {"required": [], "preferred": []}

    pairs = sections or _sniff_sections(text)
    for heading, body in pairs:
        if not body:
            continue
        buckets[_classify(heading)].append(body)

    return "\n".join(buckets["required"]), "\n".join(buckets["preferred"])


def _classify(heading):
    heading = heading or ""
    if PREFERRED_HEADING.search(heading):
        return "preferred"
    return "required"


def _sniff_sections(text):
    """Break plain text on heading-looking lines. Returns (heading, body) pairs."""
    if not text:
        return []

    pairs, heading, body = [], "", []
    for line in _break_inline_headings(text).split("\n"):
        stripped = line.strip()
        is_heading = (
            _looks_like_heading(stripped)
            and (PREFERRED_HEADING.search(stripped) or REQUIRED_HEADING.search(stripped))
        )
        if is_heading:
            if body:
                pairs.append((heading, "\n".join(body)))
            heading, body = stripped, []
        else:
            body.append(line)

    if body:
        pairs.append((heading, "\n".join(body)))
    return pairs


INLINE_HEADING_RE = re.compile(
    r"(?<=[\s.])((?:nice[- ]to[- ]have|bonus(?: points)?|preferred(?: qualifications)?|"
    r"good[- ]to[- ]have|nice extras)\s*:)",
    re.IGNORECASE,
)


def _break_inline_headings(text):
    """Put mid-sentence "Nice to have:" style markers on their own line."""
    return INLINE_HEADING_RE.sub(lambda m: "\n" + m.group(1).title() + "\n", text)


def _looks_like_heading(line):
    """Short, capitalised, unpunctuated line with few words - not a wrapped sentence."""
    if not line or len(line) > HEADING_MAX_LEN or len(line.split()) > 10:
        return False
    if not line[0].isalnum() or line[0].islower():
        return False
    return not line.endswith((".", ",", ";", ":-", "!", "?"))


def parse_experience(text):
    """Return (min_years, max_years, snippet) or (None, None, "")."""
    if not text:
        return None, None, ""

    found = []
    for match in EXPERIENCE_RE.finditer(text):
        left = text[max(0, match.start() - 70):match.start()]
        right = text[match.end():match.end() + 80]

        if EXPERIENCE_BLOCKER.search(left):
            continue
        if not (EXPERIENCE_CONTEXT.search(left) or EXPERIENCE_CONTEXT.search(right)):
            continue
        if left.rstrip().endswith(("$", "€", "£", "₹")):
            continue

        low = _to_int(match.group("lo"))
        high = _to_int(match.group("hi")) if match.group("hi") else None
        if low is None or low > MAX_PLAUSIBLE_YEARS:
            continue
        if high is not None and (high > MAX_PLAUSIBLE_YEARS or high < low):
            high = None

        snippet = " ".join(text[max(0, match.start() - 45):match.end() + 45].split())
        found.append((low, high, snippet))

    if not found:
        return None, None, ""

    low, high, snippet = min(found, key=lambda item: item[0])
    ceiling = max(item[1] or item[0] for item in found)
    return low, (ceiling if ceiling > low else high), snippet


def _to_int(token):
    if token is None:
        return None
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token)


def compile_skills(skills):
    """{"Go": ["golang", "re:...", "cs:re:..."]} -> [(canonical, [compiled, ...])].

    Plain entries are matched literally with word boundaries. Prefix `re:` for a raw
    regex, `cs:` for case-sensitive matching (needed for "Go", which is otherwise
    indistinguishable from the English verb).
    """
    compiled = []
    for canonical, patterns in skills.items():
        alternatives = []
        for pattern in patterns or [canonical]:
            flags = re.IGNORECASE
            if pattern.startswith("cs:"):
                pattern, flags = pattern[3:], 0
            if pattern.startswith("re:"):
                expression = pattern[3:]
            else:
                expression = r"(?<![\w+#.])" + re.escape(pattern) + r"(?![\w+#])"
            alternatives.append(re.compile(expression, flags))
        compiled.append((canonical, alternatives))
    return compiled


def match_skills(text, vocab):
    if not text:
        return []
    return [canonical for canonical, patterns in vocab
            if any(pattern.search(text) for pattern in patterns)]


def detect_education(text):
    if not text:
        return ""
    hits, seen = [], set()
    for pattern, keep_case in ((EDUCATION_RE, False), (EDUCATION_ABBREV_RE, True)):
        for match in pattern.finditer(text):
            raw = match.group(1).strip()
            value = raw if keep_case else raw[:1].upper() + raw[1:].lower()
            if value.lower() not in seen:
                seen.add(value.lower())
                hits.append(value)
    return ", ".join(hits[:4])


def detect_work_mode(job, text):
    declared = (job.get("work_mode") or "").strip()
    if declared:
        lowered = declared.lower()
        if "remote" in lowered:
            return "Remote"
        if "hybrid" in lowered:
            return "Hybrid"
        if "onsite" in lowered or "on-site" in lowered or "office" in lowered:
            return "Onsite"
        return declared

    haystack = f"{job.get('location', '')}\n{text[:4000]}"
    if HYBRID_RE.search(haystack):
        return "Hybrid"
    if REMOTE_RE.search(haystack):
        return "Remote"
    if ONSITE_RE.search(haystack):
        return "Onsite"
    return ""
