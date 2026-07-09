# -*- coding: utf-8 -*-
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.config.settings import settings


SUPPORTED_SUFFIXES = {".md", ".txt"}


@dataclass
class KnowledgeChunk:
    file_path: str
    title: str
    content: str
    tokens: set


class KnowledgeService:
    """Lightweight local knowledge retrieval without external dependencies."""

    def __init__(self, knowledge_dir=None, chunk_size=None):
        self.knowledge_dir = Path(knowledge_dir or settings.KNOWLEDGE_DIR)
        if not self.knowledge_dir.is_absolute():
            base_dir = Path(__file__).resolve().parents[2]
            self.knowledge_dir = base_dir / self.knowledge_dir
        self.chunk_size = int(chunk_size or settings.KNOWLEDGE_CHUNK_SIZE)
        self._chunks = None

    def list_files(self):
        if not self.knowledge_dir.exists():
            return []
        files = []
        for path in sorted(self.knowledge_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                files.append(str(path.relative_to(self.knowledge_dir)))
        return files

    def reload(self):
        self._chunks = self._load_chunks()
        return len(self._chunks)

    def search(self, query, limit=None):
        if not query or not query.strip():
            return []
        chunks = self._get_chunks()
        if not chunks:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scored = []
        for chunk in chunks:
            score = self._score(query, query_tokens, chunk)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[: int(limit or settings.KNOWLEDGE_MAX_RESULTS)]

    def _get_chunks(self):
        if self._chunks is None:
            self.reload()
        return self._chunks

    def _load_chunks(self):
        chunks = []
        for relative_file in self.list_files():
            path = self.knowledge_dir / relative_file
            text = self._read_text(path)
            if not text.strip():
                continue
            for index, content in enumerate(self._split_text(text)):
                title = f"{relative_file}#{index + 1}"
                chunks.append(KnowledgeChunk(
                    file_path=relative_file,
                    title=title,
                    content=content,
                    tokens=self._tokenize(content),
                ))
        return chunks

    @staticmethod
    def _read_text(path):
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return path.read_text(encoding="utf-8", errors="ignore")

    def _split_text(self, text):
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        sections = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
        chunks = []
        current = ""
        for section in sections:
            if len(section) > self.chunk_size:
                if current:
                    chunks.append(current.strip())
                    current = ""
                chunks.extend(self._split_long_section(section))
                continue
            if current and len(current) + len(section) + 2 > self.chunk_size:
                chunks.append(current.strip())
                current = section
            else:
                current = f"{current}\n\n{section}".strip() if current else section
        if current:
            chunks.append(current.strip())
        return chunks

    def _split_long_section(self, section):
        chunks = []
        start = 0
        while start < len(section):
            chunks.append(section[start:start + self.chunk_size].strip())
            start += self.chunk_size
        return [chunk for chunk in chunks if chunk]

    @staticmethod
    def _tokenize(text):
        lowered = text.lower()
        latin = re.findall(r"[a-z0-9_./+-]{2,}", lowered)
        chinese = re.findall(r"[\u4e00-\u9fff]{1,}", lowered)
        tokens = set(latin)
        for phrase in chinese:
            tokens.add(phrase)
            if len(phrase) > 1:
                tokens.update(phrase[i:i + 2] for i in range(len(phrase) - 1))
            if len(phrase) > 2:
                tokens.update(phrase[i:i + 3] for i in range(len(phrase) - 2))
        return tokens

    @staticmethod
    def _score(query, query_tokens, chunk):
        overlap = query_tokens & chunk.tokens
        if not overlap:
            return 0
        score = sum(2.0 if len(token) > 2 else 1.0 for token in overlap)
        if query.strip().lower() in chunk.content.lower():
            score += 5.0
        density = len(overlap) / math.sqrt(max(len(chunk.tokens), 1))
        return score + density
