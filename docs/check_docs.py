"""Check repository documentation without running stages or contacting services."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml


DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent


def headings(markdown: str) -> set[str]:
    """GitHub-compatible slugs for the simple headings used in these pages."""
    slugs: set[str] = set()
    counts: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+)$", markdown, flags=re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.replace("`", "").lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        slugs.add(f"{slug}-{count}" if count else slug)
    return slugs


def check() -> list[str]:
    errors: list[str] = []
    pages = sorted(DOCS.rglob("*.md"))
    diagram_count = 0
    for page in pages:
        text = page.read_text()
        label = str(page.relative_to(ROOT))
        front = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.S)
        if not front or not yaml.safe_load(front.group(1)).get("title"):
            errors.append(f"{label}: missing title front matter")
        opened: str | None = None
        body: list[str] = []
        prose: list[str] = []
        for line in text.splitlines():
            if line.startswith("```"):
                if opened is None:
                    opened = line[3:].strip()
                    body = []
                else:
                    try:
                        if opened == "json":
                            json.loads("\n".join(body))
                        elif opened == "yaml":
                            yaml.safe_load("\n".join(body))
                        elif opened == "mermaid":
                            diagram_count += 1
                    except (ValueError, yaml.YAMLError) as exc:
                        errors.append(f"{label}: invalid {opened} example: {exc}")
                    opened = None
            elif opened is not None:
                body.append(line)
            else:
                prose.append(line)
        if opened is not None:
            errors.append(f"{label}: unclosed code fence")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", "\n".join(prose)):
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            dest = (page.parent / unquote(parsed.path)).resolve() if parsed.path else page
            if not dest.is_file():
                errors.append(f"{label}: missing link target {target}")
            elif parsed.fragment and dest.suffix == ".md":
                if unquote(parsed.fragment) not in headings(dest.read_text()):
                    errors.append(f"{label}: missing heading {target}")

    command_dir = ROOT / "src/deposition_toolkit/commands"
    for module in command_dir.glob("*.py"):
        for node in ast.walk(ast.parse(module.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "add_parser" and node.args and isinstance(node.args[0], ast.Constant):
                    name = node.args[0].value
                    if not (DOCS / "commands" / f"{name}.md").is_file():
                        errors.append(f"Missing internals page for registered command {name}")
    for config in DOCS.glob("*.yml"):
        try:
            yaml.safe_load(config.read_text())
        except yaml.YAMLError as exc:
            errors.append(f"{config.name}: invalid YAML: {exc}")
    print(f"Checked {len(pages)} pages and {diagram_count} Mermaid blocks.")
    return errors


if __name__ == "__main__":
    failures = check()
    for failure in failures:
        print(failure, file=sys.stderr)
    if not failures:
        print("Documentation checks passed.")
    raise SystemExit(bool(failures))
