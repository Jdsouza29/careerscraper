"""HTML to readable-text conversion, shared by every adapter."""

import html
import re

from bs4 import BeautifulSoup

BLANK_LINES = re.compile(r"\n{3,}")
TRAILING_SPACE = re.compile(r"[ \t]+\n")


def html_to_text(raw):
    """Turn a job-description HTML blob into plain text with bullets preserved.

    Some boards (Greenhouse) return HTML that is itself entity-escaped, so unescape
    until it stops changing before parsing.
    """
    if not raw:
        return ""

    text = str(raw)
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped

    if "<" not in text:
        return clean_whitespace(text)

    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for li in soup.find_all("li"):
        li.insert(0, "- ")
    for br in soup.find_all("br"):
        br.replace_with("\n")

    return clean_whitespace(soup.get_text("\n"))


def clean_whitespace(text):
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    text = "\n".join(lines)
    text = TRAILING_SPACE.sub("\n", text)
    return BLANK_LINES.sub("\n\n", text).strip()


def dig(payload, path, default=None):
    """Read a dotted path out of nested dicts/lists: "data.jobs.0.title"."""
    current = payload
    for part in str(path).split("."):
        if current is None:
            return default
        if isinstance(current, list):
            if not part.lstrip("-").isdigit():
                return default
            index = int(part)
            if index >= len(current) or index < -len(current):
                return default
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
        else:
            return default
    return default if current is None else current
