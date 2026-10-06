"""tools/bundle.py: Single-file distribution bundler for Royal Caribbean utilities.

Flattens submodules into a single-file distribution script with clean, deduplicated,
and properly sorted top-level imports.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List, Set

TOOLS_DIR = Path(__file__).parent.resolve()
ROOT_DIR = TOOLS_DIR.parent
PACKAGE_DIR = ROOT_DIR / "royal_caribbean"
DIST_DIR = ROOT_DIR / "dist"

ENTRY_POINT = ROOT_DIR / "CheckRoyalCaribbeanCasinoOffers_poc.py"
DIST_OUTPUT = DIST_DIR / "CheckRoyalCaribbeanCasinoOffers_bundled.py"

MODULE_BUILD_ORDER = [
    PACKAGE_DIR / "utils" / "constants.py",
    PACKAGE_DIR / "utils" / "logging.py",
    PACKAGE_DIR / "config" / "loaders.py",
    PACKAGE_DIR / "api" / "client.py",
    PACKAGE_DIR / "api" / "auth.py",
    PACKAGE_DIR / "core" / "casino.py",
]


class PackageImportExtractor(ast.NodeTransformer):
    """AST Transformer to extract external imports for header deduplication while preserving

    imports enclosed within try/except blocks.
    """

    def __init__(self, package_name: str = "royal_caribbean") -> None:
        super().__init__()
        self.package_name = package_name
        self.plain_imports: Set[str] = set()
        self.from_imports: Dict[str, Set[str]] = {}
        self.future_imports: Set[str] = set()
        self._in_try_block: bool = False  # Track nesting depth/context

    def visit_Try(self, node: ast.Try) -> ast.AST:
        """Preserves try/except structures by flagging child imports so they are

        not stripped or hoisted to the top level.
        """
        previous_state = self._in_try_block
        self._in_try_block = True
        self.generic_visit(node)
        self._in_try_block = previous_state
        return node

    def visit_Import(self, node: ast.Import) -> ast.AST | None:
        # DO NOT hoist or strip imports inside try/except blocks!
        if self._in_try_block:
            return node

        for alias in node.names:
            if not alias.name.startswith(self.package_name):
                name_str = alias.name + (
                    f" as {alias.asname}" if alias.asname else ""
                )
                self.plain_imports.add(name_str)

        # Strip standard top-level import statements from the module body
        return None

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST | None:
        # DO NOT hoist or strip imports inside try/except blocks!
        if self._in_try_block:
            return node

        # Strip relative imports (. or ..)
        if node.level and node.level > 0:
            return None

        module_name = node.module or ""

        # Ignore internal package imports
        if module_name == self.package_name or module_name.startswith(
            f"{self.package_name}."
        ):
            return None

        # Capture __future__ imports separately
        if module_name == "__future__":
            for alias in node.names:
                self.future_imports.add(alias.name)
            return None

        # Collect and group external 'from' imports by module name
        if module_name not in self.from_imports:
            self.from_imports[module_name] = set()

        for alias in node.names:
            name_str = alias.name + (
                f" as {alias.asname}" if alias.asname else ""
            )
            self.from_imports[module_name].add(name_str)

        # Strip standard top-level import statements from the module body
        return None


def process_module(source_code: str) -> tuple[str, PackageImportExtractor]:
    """Parses AST, extracts all imports, and returns cleaned module body code."""
    tree = ast.parse(source_code)
    extractor = PackageImportExtractor()
    modified_tree = extractor.visit(tree)
    ast.fix_missing_locations(modified_tree)

    cleaned_code = ast.unparse(modified_tree)
    return cleaned_code, extractor


def build_import_header(extractors: List[PackageImportExtractor]) -> str:
    """Consolidates and formats deduplicated imports according to PEP 8 standards."""
    all_future: Set[str] = set()
    all_plain: Set[str] = set()
    all_from: Dict[str, Set[str]] = {}

    for ext in extractors:
        all_future.update(ext.future_imports)
        all_plain.update(ext.plain_imports)

        for mod, names in ext.from_imports.items():
            if mod not in all_from:
                all_from[mod] = set()
            all_from[mod].update(names)

    lines: List[str] = []

    # 1. __future__ imports first
    if all_future:
        sorted_future = ", ".join(sorted(all_future))
        lines.append(f"from __future__ import {sorted_future}")

    # 2. Plain imports ('import foo') sorted alphabetically
    if all_plain:
        for name in sorted(all_plain):
            lines.append(f"import {name}")

    # 3. 'from foo import bar' grouped by module and sorted alphabetically
    if all_from:
        for mod in sorted(all_from.keys()):
            sorted_names = ", ".join(sorted(all_from[mod]))
            lines.append(f"from {mod} import {sorted_names}")

    return "\n".join(lines)


def build_bundle() -> None:
    """Builds the single-file distribution bundle."""
    print(f"📦 Building standalone bundle for {ENTRY_POINT.name}...")

    DIST_DIR.mkdir(parents=True, exist_ok=True)

    extractors: List[PackageImportExtractor] = []
    bundled_blocks: List[str] = []

    # 1. Process package modules in dependency order
    for module_path in MODULE_BUILD_ORDER:
        if not module_path.exists():
            print(f"⚠️  Skipping missing module: {module_path.relative_to(ROOT_DIR)}")
            continue

        print(f"  └─ Bundling submodule: {module_path.relative_to(ROOT_DIR)}")
        source = module_path.read_text(encoding="utf-8")

        cleaned_code, extractor = process_module(source)
        extractors.append(extractor)

        rel_path = module_path.relative_to(ROOT_DIR)
        block_header = f"\n# {'=' * 70}\n# MODULE: {rel_path}\n# {'=' * 70}\n"
        bundled_blocks.append(block_header + cleaned_code)

    # 2. Process entry point
    if ENTRY_POINT.exists():
        print(f"  └─ Processing entrypoint: {ENTRY_POINT.name}")
        entry_source = ENTRY_POINT.read_text(encoding="utf-8")

        cleaned_code, extractor = process_module(entry_source)
        extractors.append(extractor)

        block_header = (
            f"\n# {'=' * 70}\n# MAIN ENTRYPOINT: {ENTRY_POINT.name}\n# {'=' * 70}\n"
        )
        bundled_blocks.append(block_header + cleaned_code)

    # 3. Build unified header
    import_header = build_import_header(extractors)
    header_section = f'"""Single-file distribution bundle generated by bundle.py for {ENTRY_POINT.name}."""\n\n'
    header_section += import_header + "\n\n"

    # 4. Combine and write output
    full_output = header_section + "\n".join(bundled_blocks) + "\n"
    full_output = re.sub(r"\n{3,}", "\n\n", full_output)

    DIST_OUTPUT.write_text(full_output, encoding="utf-8")
    print(
        f"\n✅ Bundle successfully generated at: {DIST_OUTPUT.relative_to(ROOT_DIR)}"
    )


if __name__ == "__main__":
    build_bundle()