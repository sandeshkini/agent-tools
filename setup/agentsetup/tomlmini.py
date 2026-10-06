"""TOML reader: tomllib on Python 3.11+, else a small parser for the subset profiles use.

The subset: comments, [table] / [a.b-c] headers, key = value with basic or literal strings, integers,
floats, booleans, arrays (one or more lines) and simple inline tables. macOS's /usr/bin/python3 is 3.9,
which has no tomllib, and setup must run there with the standard library only.
"""
import re

try:
    import tomllib as _tomllib          # Python 3.11+
except ImportError:                     # pragma: no cover - depends on the interpreter
    _tomllib = None


class TOMLError(ValueError):
    pass


def loads(text, force_mini=False):
    if _tomllib and not force_mini:
        try:
            return _tomllib.loads(text)
        except _tomllib.TOMLDecodeError as e:
            raise TOMLError(str(e))
    return _Parser(text).parse()


_BARE = re.compile(r"[A-Za-z0-9_-]+")
_ESC = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}


class _Parser:
    def __init__(self, text):
        self.s = text
        self.i = 0
        self.line = 1

    def err(self, msg):
        raise TOMLError(f"line {self.line}: {msg}")

    def peek(self):
        return self.s[self.i] if self.i < len(self.s) else ""

    def skip_ws(self, newlines=False):
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t\r":
                self.i += 1
            elif c == "\n" and newlines:
                self.i += 1
                self.line += 1
            elif c == "#":
                while self.i < len(self.s) and self.s[self.i] != "\n":
                    self.i += 1
            else:
                break

    def end_of_line(self):
        self.skip_ws()
        if self.i < len(self.s):
            if self.s[self.i] != "\n":
                self.err(f"unexpected {self.s[self.i]!r}")
            self.i += 1
            self.line += 1

    def key(self):
        parts = []
        while True:
            self.skip_ws()
            c = self.peek()
            if c == '"':
                parts.append(self.basic_string())
            elif c == "'":
                parts.append(self.literal_string())
            else:
                m = _BARE.match(self.s, self.i)
                if not m:
                    self.err("expected a key")
                parts.append(m.group())
                self.i = m.end()
            self.skip_ws()
            if self.peek() == ".":
                self.i += 1
                continue
            return parts

    def table(self, root, parts):
        t = root
        for p in parts:
            t = t.setdefault(p, {})
            if not isinstance(t, dict):
                self.err(f"{'.'.join(parts)} is not a table")
        return t

    def parse(self):
        root = {}
        cur = root
        while True:
            self.skip_ws(newlines=True)
            if self.i >= len(self.s):
                return root
            if self.peek() == "[":
                self.i += 1
                if self.peek() == "[":
                    self.err("arrays of tables are not supported")
                parts = self.key()
                if self.peek() != "]":
                    self.err("expected ]")
                self.i += 1
                cur = self.table(root, parts)
                self.end_of_line()
                continue
            parts = self.key()
            if self.peek() != "=":
                self.err("expected =")
            self.i += 1
            self.skip_ws()
            val = self.value()
            t = self.table(cur, parts[:-1])
            if parts[-1] in t:
                self.err(f"duplicate key {'.'.join(parts)}")
            t[parts[-1]] = val
            self.end_of_line()

    def value(self):
        c = self.peek()
        if c == '"':
            return self.basic_string()
        if c == "'":
            return self.literal_string()
        if c == "[":
            return self.array()
        if c == "{":
            return self.inline_table()
        m = re.compile(r"true|false|[+-]?[0-9][0-9_]*(\.[0-9_]+)?([eE][+-]?[0-9]+)?").match(self.s, self.i)
        if not m:
            self.err("expected a value")
        self.i = m.end()
        tok = m.group()
        if tok in ("true", "false"):
            return tok == "true"
        tok = tok.replace("_", "")
        return float(tok) if any(x in tok for x in ".eE") else int(tok)

    def basic_string(self):
        if self.s.startswith('"""', self.i):
            self.err("multi-line strings are not supported")
        self.i += 1
        out = []
        while True:
            if self.i >= len(self.s) or self.s[self.i] == "\n":
                self.err("unterminated string")
            c = self.s[self.i]
            self.i += 1
            if c == '"':
                return "".join(out)
            if c == "\\":
                e = self.s[self.i:self.i + 1]
                self.i += 1
                if e in _ESC:
                    out.append(_ESC[e])
                elif e in ("u", "U"):
                    n = 4 if e == "u" else 8
                    out.append(chr(int(self.s[self.i:self.i + n], 16)))
                    self.i += n
                else:
                    self.err(f"bad escape \\{e}")
            else:
                out.append(c)

    def literal_string(self):
        if self.s.startswith("'''", self.i):
            self.err("multi-line strings are not supported")
        j = self.s.find("'", self.i + 1)
        if j < 0 or "\n" in self.s[self.i:j]:
            self.err("unterminated string")
        v = self.s[self.i + 1:j]
        self.i = j + 1
        return v

    def array(self):
        self.i += 1
        out = []
        while True:
            self.skip_ws(newlines=True)
            if self.peek() == "]":
                self.i += 1
                return out
            out.append(self.value())
            self.skip_ws(newlines=True)
            if self.peek() == ",":
                self.i += 1
            elif self.peek() != "]":
                self.err("expected , or ] in array")

    def inline_table(self):
        self.i += 1
        out = {}
        self.skip_ws()
        if self.peek() == "}":
            self.i += 1
            return out
        while True:
            parts = self.key()
            if self.peek() != "=":
                self.err("expected = in inline table")
            self.i += 1
            self.skip_ws()
            self.table(out, parts[:-1])[parts[-1]] = self.value()
            self.skip_ws()
            if self.peek() == ",":
                self.i += 1
            elif self.peek() == "}":
                self.i += 1
                return out
            else:
                self.err("expected , or } in inline table")
