"""Minimal, quote-aware s-expression tokenizer/parser/serializer."""

from __future__ import annotations

from typing import List, Optional, Tuple


def tokenize(text: str) -> List[str]:
    toks: List[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            toks.append(text[i : j + 1])
            i = j + 1
            continue
        if c == "(" or c == ")":
            toks.append(c)
            i += 1
            continue
        j = i
        while j < n and text[j] not in ' \t\r\n()"':
            j += 1
        toks.append(text[i:j])
        i = j
    return toks


def parse(text: str):
    """Parse into nested python lists (atoms keep quotes for value tokens)."""
    stack = []
    top = []
    for tok in tokenize(text):
        if tok == "(":
            node = []
            if stack:
                stack[-1].append(node)
            else:
                top.append(node)
            stack.append(node)
        elif tok == ")":
            if stack:
                stack.pop()
        else:
            if stack:
                stack[-1].append(tok)
            else:
                top.append(tok)
    return top


def atom(item) -> str:
    return item if isinstance(item, str) else ""


def unatom(item: str) -> str:
    if isinstance(item, str) and item.startswith('"') and item.endswith('"') and len(item) >= 2:
        inner = item[1:-1]
        out = inner.replace('\\"', '"')
        return out
    return item if isinstance(item, str) else ""


def find_first(node: list, key: str) -> Optional[list]:
    for it in node:
        if isinstance(it, list) and it and it[0] == key:
            return it
    return None


def find_all(node: list, key: str) -> List[list]:
    out = []
    def walk(n):
        for it in n:
            if isinstance(it, list):
                if it and it[0] == key:
                    out.append(it)
                walk(it)
    walk(node)
    return out


def fnum(tok) -> float:
    return float(tok)


def fmt_num(x: float, dp: int = 4) -> str:
    s = f"{x:.{dp}f}".rstrip("0").rstrip(".")
    return s if s else "0"


def sym_name(item: str) -> str:
    """Strip quotes and return the atom value used for symbol names."""
    return unatom(item)
