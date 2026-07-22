from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ingestion.parser import ParsedSection


@dataclass
class CodeChunk:
    chunk_id: str
    filename: str
    language: str
    section_name: str
    section_type: str
    content: str
    start_line: int
    end_line: int
    chunk_index: int
    token_estimate: int

    def __repr__(self):
        return (
            f"CodeChunk("
            f"{self.filename} "
            f"{self.section_name}[{self.chunk_index}] "
            f"~{self.token_estimate}tok)"
        )


class Chunker:

    CHARS_PER_TOKEN = 4

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 128,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        self._char_limit = (
            self.chunk_size * self.CHARS_PER_TOKEN
        )

        self._overlap_chars = (
            self.chunk_overlap * self.CHARS_PER_TOKEN
        )

    def chunk_sections(
        self,
        sections: list[ParsedSection],
    ) -> list[CodeChunk]:

        all_chunks = []

        for section in sections:
            all_chunks.extend(
                self._chunk_section(section)
            )

        total_tokens = sum(
            c.token_estimate
            for c in all_chunks
        )

        print(
            f"[chunker] "
            f"{len(sections)} sections -> "
            f"{len(all_chunks)} chunks "
            f"(~{total_tokens} tokens)"
        )

        return all_chunks

    def _chunk_section(
        self,
        section: ParsedSection,
    ) -> list[CodeChunk]:

        content = section.content.strip()

        if not content:
            return []

        if len(content) <= self._char_limit:

            return [
                self._make_chunk(
                    section,
                    content,
                    0,
                    section.start_line,
                    section.end_line,
                )
            ]

        lines = content.splitlines()

        chunks = []

        chunk_idx = 0
        i = 0

        while i < len(lines):

            window_lines = []
            char_count = 0

            j = i

            while (
                j < len(lines)
                and char_count + len(lines[j]) + 1
                <= self._char_limit
            ):
                window_lines.append(lines[j])
                char_count += len(lines[j]) + 1
                j += 1

            if not window_lines:
                window_lines = [lines[i]]
                j = i + 1

            chunk_text = "\n".join(window_lines)

            abs_start = section.start_line + i
            abs_end = section.start_line + j - 1

            chunks.append(
                self._make_chunk(
                    section,
                    chunk_text,
                    chunk_idx,
                    abs_start,
                    abs_end,
                )
            )

            chunk_idx += 1

            overlap_remaining = self._overlap_chars

            step = j - i

            while (
                step > 1
                and overlap_remaining > 0
            ):
                step -= 1

                overlap_remaining -= (
                    len(lines[i + step - 1]) + 1
                )

            i += max(1, step)

        return chunks

    def _make_chunk(
        self,
        section: ParsedSection,
        content: str,
        idx: int,
        start_line: int,
        end_line: int,
    ) -> CodeChunk:

        chunk_id = hashlib.sha256(
            (
                f"{section.filename}:"
                f"{section.name}:"
                f"{idx}:"
                f"{content}"
            ).encode()
        ).hexdigest()[:16]

        return CodeChunk(
            chunk_id=chunk_id,
            filename=section.filename,
            language=section.language,
            section_name=section.name,
            section_type=section.section_type,
            content=content,
            start_line=start_line,
            end_line=end_line,
            chunk_index=idx,
            token_estimate=max(
                1,
                round(
                    len(content)
                    / self.CHARS_PER_TOKEN
                ),
            ),
        )


if __name__ == "__main__":

    from ingestion.parser import ParsedSection

    section = ParsedSection(
        filename="auth.py",
        language="python",
        section_type="function",
        name="login",
        content="\n".join(
            [f"line {i}" for i in range(200)]
        ),
        start_line=1,
        end_line=200,
    )

    chunker = Chunker(
        chunk_size=100,
        chunk_overlap=20,
    )

    chunks = chunker.chunk_sections([section])

    for chunk in chunks:
        print(chunk)