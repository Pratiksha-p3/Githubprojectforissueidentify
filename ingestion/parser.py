"""
ingestion/parser.py

Reads raw file content from PRFile objects.
Extracts clean text and splits into logical sections
(functions, classes, modules).

Data flow:
PRFile.full_content -> list[ParsedSection]
"""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass

from ingestion.github_loader import PRFile


@dataclass
class ParsedSection:
    filename: str
    language: str
    section_type: str  # function | class | module
    name: str
    content: str
    start_line: int
    end_line: int

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    def __repr__(self):
        return (
            f"ParsedSection("
            f"{self.filename}:{self.section_type} "
            f"{self.name} "
            f"L{self.start_line}-{self.end_line})"
        )


class Parser:
    """
    Splits source files into logical sections.

    Benefits:
    - Better embeddings
    - Better retrieval quality
    - Better code review context
    """

    def parse(self, pf: PRFile) -> list[ParsedSection]:

        if not pf.full_content.strip():
            return []

        if pf.language == "python":
            sections = self._parse_python(pf)

        elif pf.language in ("javascript", "typescript"):
            sections = self._parse_js_ts(pf)

        elif pf.language == "go":
            sections = self._parse_go(pf)

        elif pf.language == "java":
            sections = self._parse_java(pf)

        else:
            sections = self._parse_generic(pf)

        if not sections:
            sections = self._parse_generic(pf)

        return sections

    def parse_many(self, files: list[PRFile]) -> list[ParsedSection]:

        all_sections = []

        for pf in files:
            sections = self.parse(pf)
            all_sections.extend(sections)

            print(
                f"[parser] {pf.filename} "
                f"-> {len(sections)} sections"
            )

        return all_sections

    # ==========================================================
    # Python
    # ==========================================================

    def _parse_python(self, pf: PRFile) -> list[ParsedSection]:

        lines = pf.full_content.splitlines()
        sections = []

        i = 0

        while i < len(lines):

            line = lines[i]

            cls_match = re.match(r"^\s*class\s+(\w+)", line)

            if cls_match:

                name = cls_match.group(1)
                start = i

                body_lines = [line]
                i += 1

                while i < len(lines):

                    next_line = lines[i]

                    if (
                        next_line.strip()
                        and not next_line.startswith((" ", "\t"))
                    ):
                        if next_line.strip().startswith(
                            ("class ", "def ", "async def ")
                        ):
                            break

                    body_lines.append(next_line)
                    i += 1

                sections.append(
                    ParsedSection(
                        filename=pf.filename,
                        language=pf.language,
                        section_type="class",
                        name=name,
                        content="\n".join(body_lines),
                        start_line=start + 1,
                        end_line=start + len(body_lines),
                    )
                )

                continue

            fn_match = re.match(
                r"^\s*(?:async\s+def|def)\s+(\w+)",
                line,
            )

            if fn_match:

                name = fn_match.group(1)
                start = i

                body_lines = [line]
                i += 1

                while i < len(lines):

                    next_line = lines[i]

                    if (
                        next_line.strip()
                        and not next_line.startswith((" ", "\t"))
                    ):
                        if next_line.strip().startswith(
                            ("def ", "async def ", "class ")
                        ):
                            break

                    body_lines.append(next_line)
                    i += 1

                sections.append(
                    ParsedSection(
                        filename=pf.filename,
                        language=pf.language,
                        section_type="function",
                        name=name,
                        content="\n".join(body_lines),
                        start_line=start + 1,
                        end_line=start + len(body_lines),
                    )
                )

                continue

            i += 1

        if not sections:
            return self._whole_file_section(pf)

        return sections

    # ==========================================================
    # JavaScript / TypeScript
    # ==========================================================

    def _parse_js_ts(self, pf: PRFile) -> list[ParsedSection]:

        lines = pf.full_content.splitlines()
        sections = []

        patterns = [
            re.compile(
                r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)"
            ),
            re.compile(
                r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*="
            ),
            re.compile(
                r"^(?:export\s+)?class\s+(\w+)"
            ),
        ]

        i = 0

        while i < len(lines):

            line = lines[i]

            matched_name = None
            matched_type = "function"

            for pat in patterns:

                match = pat.match(line)

                if match:
                    matched_name = match.group(1)

                    if "class" in line:
                        matched_type = "class"

                    break

            if matched_name:

                start = i

                brace_count = (
                    line.count("{") - line.count("}")
                )

                body_lines = [line]
                i += 1

                while i < len(lines):

                    body_lines.append(lines[i])

                    brace_count += (
                        lines[i].count("{")
                        - lines[i].count("}")
                    )

                    i += 1

                    if brace_count <= 0:
                        break

                sections.append(
                    ParsedSection(
                        filename=pf.filename,
                        language=pf.language,
                        section_type=matched_type,
                        name=matched_name,
                        content="\n".join(body_lines),
                        start_line=start + 1,
                        end_line=start + len(body_lines),
                    )
                )

                continue

            i += 1

        return sections or self._whole_file_section(pf)

    # ==========================================================
    # Go
    # ==========================================================

    def _parse_go(self, pf: PRFile) -> list[ParsedSection]:

        lines = pf.full_content.splitlines()
        sections = []

        fn_pat = re.compile(
            r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)"
        )

        i = 0

        while i < len(lines):

            match = fn_pat.match(lines[i])

            if match:

                name = match.group(1)
                start = i

                brace_count = (
                    lines[i].count("{")
                    - lines[i].count("}")
                )

                body_lines = [lines[i]]
                i += 1

                while i < len(lines) and brace_count > 0:

                    body_lines.append(lines[i])

                    brace_count += (
                        lines[i].count("{")
                        - lines[i].count("}")
                    )

                    i += 1

                sections.append(
                    ParsedSection(
                        filename=pf.filename,
                        language=pf.language,
                        section_type="function",
                        name=name,
                        content="\n".join(body_lines),
                        start_line=start + 1,
                        end_line=start + len(body_lines),
                    )
                )

                continue

            i += 1

        return sections or self._whole_file_section(pf)

    # ==========================================================
    # Java
    # ==========================================================

    def _parse_java(self, pf: PRFile) -> list[ParsedSection]:

        lines = pf.full_content.splitlines()
        sections = []

        pat = re.compile(
            r"^\s*(public|private|protected).*?\s+(\w+)\s*\("
        )

        i = 0

        while i < len(lines):

            match = pat.match(lines[i])

            if match and "{" in lines[i]:

                name = match.group(2)
                start = i

                brace_count = (
                    lines[i].count("{")
                    - lines[i].count("}")
                )

                body_lines = [lines[i]]
                i += 1

                while i < len(lines) and brace_count > 0:

                    body_lines.append(lines[i])

                    brace_count += (
                        lines[i].count("{")
                        - lines[i].count("}")
                    )

                    i += 1

                sections.append(
                    ParsedSection(
                        filename=pf.filename,
                        language=pf.language,
                        section_type="function",
                        name=name,
                        content="\n".join(body_lines),
                        start_line=start + 1,
                        end_line=start + len(body_lines),
                    )
                )

                continue

            i += 1

        return sections or self._whole_file_section(pf)

    # ==========================================================
    # Generic
    # ==========================================================

    def _parse_generic(
        self,
        pf: PRFile,
    ) -> list[ParsedSection]:

        return self._whole_file_section(pf)

    def _whole_file_section(
        self,
        pf: PRFile,
    ) -> list[ParsedSection]:

        lines = pf.full_content.splitlines()

        return [
            ParsedSection(
                filename=pf.filename,
                language=pf.language,
                section_type="module",
                name=Path(pf.filename).stem,
                content=pf.full_content,
                start_line=1,
                end_line=max(len(lines), 1),
            )
        ]


# ==========================================================
# Test
# ==========================================================

if __name__ == "__main__":

    sample = PRFile(
        filename="sample.py",
        language="python",
        full_content="""
class User:

    def login(self):
        pass


def main():
    print("hello")
"""
    )

    parser = Parser()

    sections = parser.parse(sample)

    for section in sections:
        print(section)
        