#!/usr/bin/env python3
"""Reformat a minified JS bundle into readable, indented source.

The Eventworx ``app.js`` ships as a ~9.6 MB Google Closure Compiler bundle on a
single line. This makes it impossible to navigate by line number and awkward to
read. This script runs it through ``jsbeautifier`` (a proper JS tokenizer that
respects string/regex/template literals) to produce an indented copy you can
grep and read by line.

Identifier names stay mangled (the minifier already discarded the originals) but
the structure becomes legible and string literals keep their byte content.

Usage (run from the repo root):
    python helpers/beautify_app.py                  # cache/app.js -> cache/app.pretty.js
    python helpers/beautify_app.py in.js out.js     # explicit input and output

Install the one dependency first:
    pip install jsbeautifier
"""
import sys
import time
from pathlib import Path

try:
    import jsbeautifier
except ImportError:
    sys.exit("jsbeautifier is not installed. Run:  pip install jsbeautifier")

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else CACHE_DIR / "app.js"
    if len(sys.argv) > 2:
        dst = Path(sys.argv[2])
    else:
        dst = src.with_suffix(".pretty" + src.suffix)

    if not src.exists():
        sys.exit(f"Input file not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    opts = jsbeautifier.default_options()
    opts.indent_size = 2
    opts.preserve_newlines = False      # the input is one line; ignore it
    opts.max_preserve_newlines = 1
    opts.break_chained_methods = False  # keep a.b().c() on one line — less noise
    opts.keep_array_indentation = False

    size_mb = src.stat().st_size / 1_048_576
    print(f"Beautifying {src} ({size_mb:.1f} MB) -> {dst}")
    print("Large bundles can take a minute or two...")

    start = time.time()
    code = src.read_text(encoding="utf-8")
    pretty = jsbeautifier.beautify(code, opts)
    dst.write_text(pretty, encoding="utf-8")

    out_mb = dst.stat().st_size / 1_048_576
    lines = pretty.count("\n") + 1
    print(f"Done in {time.time() - start:.0f}s: {out_mb:.1f} MB, {lines:,} lines.")


if __name__ == "__main__":
    main()
