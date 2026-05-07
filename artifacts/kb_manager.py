"""
kb_manager.py
=============
Document ingestion and management layer for the knowledge base.
Handles parsing PDF, CSV, and TXT files into text chunks,
indexing them into ChromaDB, and managing concept documents.

Imported by microservices.py for the /kb/* API endpoints.
"""

import os
import re
from pathlib import Path


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _split_into_chunks(text: str, max_words: int = 500, overlap: int = 50) -> list[str]:
    """
    Splits a long text into chunks of max_words words with overlap.
    Used when a page or paragraph exceeds the word limit.
    """
    words = text.split()
    if len(words) <= max_words:
        return [text]

    chunks = []
    start = 0
    while start < len(words):
        end = start + max_words
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        if end >= len(words):
            break
        start = end - overlap  # step back by overlap for continuity
    return chunks


# ─────────────────────────────────────────────────────────────────
# FILE PARSERS
# ─────────────────────────────────────────────────────────────────

def parse_pdf(filepath: str) -> list[str]:
    """
    Extracts text from a PDF file using pymupdf (fitz).
    Splits by page. Each page becomes one chunk if under 500 words.
    Pages exceeding 500 words are split into 500-word chunks with
    50-word overlap.

    Returns: list of text strings (one per chunk).
    Raises:  ValueError if file is empty or unreadable.
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        raise ValueError("pymupdf is not installed. Run: pip install pymupdf")

    doc = fitz.open(filepath)
    if doc.page_count == 0:
        raise ValueError(f"PDF has no pages: {filepath}")

    chunks = []
    for page_num in range(doc.page_count):
        page = doc[page_num]
        text = page.get_text().strip()
        if not text:
            continue
        page_chunks = _split_into_chunks(text, max_words=500, overlap=50)
        chunks.extend(page_chunks)

    doc.close()

    if not chunks:
        raise ValueError(f"PDF produced no text content: {filepath}")

    return chunks


def parse_csv(filepath: str) -> list[str]:
    """
    Reads a CSV file and converts each row to a natural language
    sentence for embedding.
    Format: "<col1>: <val1>, <col2>: <val2>, ..."
    Groups rows into chunks of 10 rows each.

    Returns: list of text strings (one per chunk).
    Raises:  ValueError if file has no readable rows.
    """
    try:
        import pandas as pd
    except ImportError:
        raise ValueError("pandas is not installed. Run: pip install pandas")

    df = pd.read_csv(filepath)
    if df.empty:
        raise ValueError(f"CSV file has no readable rows: {filepath}")

    rows_as_text = []
    for _, row in df.iterrows():
        parts = [f"{col}: {val}" for col, val in row.items() if pd.notna(val)]
        rows_as_text.append(", ".join(parts))

    if not rows_as_text:
        raise ValueError(f"CSV produced no text content: {filepath}")

    # Group into chunks of 10 rows
    chunk_size = 10
    chunks = []
    for i in range(0, len(rows_as_text), chunk_size):
        chunk = "\n".join(rows_as_text[i:i + chunk_size])
        chunks.append(chunk)

    return chunks


def parse_txt(filepath: str) -> list[str]:
    """
    Reads a plain text file. Splits into chunks at blank lines
    (paragraphs). Paragraphs exceeding 500 words are split further
    into 500-word chunks with 50-word overlap.

    Returns: list of text strings.
    Raises:  ValueError if file is empty.
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read().strip()

    if not content:
        raise ValueError(f"TXT file is empty: {filepath}")

    # Split on one or more blank lines
    paragraphs = re.split(r"\n\s*\n", content)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks = []
    for para in paragraphs:
        sub_chunks = _split_into_chunks(para, max_words=500, overlap=50)
        chunks.extend(sub_chunks)

    return chunks


# ─────────────────────────────────────────────────────────────────
# DOCUMENT MANAGEMENT
# ─────────────────────────────────────────────────────────────────

def index_document(filename: str, chunks: list[str], source_type: str,
                   collection) -> list[str]:
    """
    Indexes a list of text chunks into ChromaDB.
    Generates IDs in the format: <filename_stem>_chunk_<nnnn>
    where n is zero-padded to 4 digits.

    Stores metadata:
        source_file:  filename
        source_type:  "pdf" / "csv" / "txt" / "concept"
        chunk_index:  n
        total_chunks: len(chunks)

    Returns: list of generated IDs.
    """
    stem = Path(filename).stem
    ids       = []
    documents = []
    metadatas = []

    for i, chunk in enumerate(chunks):
        chunk_id = f"{stem}_chunk_{i:04d}"
        ids.append(chunk_id)
        documents.append(chunk)
        metadatas.append({
            "source_file":  filename,
            "source_type":  source_type,
            "chunk_index":  i,
            "total_chunks": len(chunks),
        })

    collection.add(ids=ids, documents=documents, metadatas=metadatas)
    return ids


def delete_document(filename_stem: str, collection) -> int:
    """
    Deletes all chunks associated with a source file.
    Matches by metadata field source_file containing filename_stem.

    Returns: number of chunks deleted.
    """
    # Get all documents and filter by source_file
    all_results = collection.get(include=["metadatas"])
    ids_to_delete = [
        doc_id
        for doc_id, meta in zip(all_results["ids"], all_results["metadatas"])
        if filename_stem in meta.get("source_file", "")
    ]

    if ids_to_delete:
        collection.delete(ids=ids_to_delete)

    return len(ids_to_delete)


def list_documents(collection) -> list[dict]:
    """
    Returns a summary of all documents in the collection.
    Groups chunks by source_file and returns one entry per unique
    source_file with: filename, source_type, chunk_count, doc_ids.
    Handles both kb_manager metadata format (source_file/source_type)
    and knowledge_base.py build format (id/type).
    """
    all_results = collection.get(include=["metadatas"])

    groups: dict[str, dict] = {}
    for doc_id, meta in zip(all_results["ids"], all_results["metadatas"]):
        # kb_manager format uses source_file; build_knowledge_base uses id
        source_file  = meta.get("source_file") or meta.get("id", doc_id)
        # kb_manager uses source_type; build_knowledge_base uses type
        raw_type     = meta.get("source_type") or meta.get("type", "unknown")
        # Normalise concept subtypes to "concept"
        if "concept" in raw_type:
            source_type = "concept"
        else:
            source_type = raw_type

        if source_file not in groups:
            groups[source_file] = {
                "filename":    source_file,
                "source_type": source_type,
                "chunk_count": 0,
                "doc_ids":     [],
            }
        groups[source_file]["chunk_count"] += 1
        groups[source_file]["doc_ids"].append(doc_id)

    return list(groups.values())


def concepts_are_enabled(collection) -> bool:
    """
    Returns True if any concept documents are currently indexed.
    Checks both source_type="concept" (kb_manager format) and
    type containing "concept" (knowledge_base.py build format).
    """
    all_results = collection.get(include=["metadatas"])
    for meta in all_results["metadatas"]:
        if meta.get("source_type") == "concept":
            return True
        # Also match docs indexed directly by build_knowledge_base
        # which store type as "constraint_concept", "risk_concept", etc.
        if "concept" in meta.get("type", "") or "system_concept" in meta.get("type", ""):
            return True
    return False


# ─────────────────────────────────────────────────────────────────
# CONCEPT DOCUMENT MANAGEMENT
# ─────────────────────────────────────────────────────────────────

def enable_concepts(collection) -> int:
    """
    Indexes all concept documents from knowledge_base.py into the
    collection. Uses source_type="concept" and source_file=doc["id"].
    Does nothing if concepts are already enabled.

    Returns: number of chunks indexed (0 if already enabled).
    """
    if concepts_are_enabled(collection):
        return 0

    from knowledge_base import DOCUMENTS

    total = 0
    for doc in DOCUMENTS:
        ids = index_document(
            filename=doc["id"],
            chunks=[doc["content"]],
            source_type="concept",
            collection=collection,
        )
        total += len(ids)

    return total


def disable_concepts(collection) -> int:
    """
    Removes all documents with source_type="concept" or type containing
    "concept" from the collection (handles both indexed formats).

    Returns: number of chunks removed.
    """
    all_results = collection.get(include=["metadatas"])
    ids_to_delete = []
    for doc_id, meta in zip(all_results["ids"], all_results["metadatas"]):
        if meta.get("source_type") == "concept":
            ids_to_delete.append(doc_id)
        elif "concept" in meta.get("type", "") or "system_concept" in meta.get("type", ""):
            ids_to_delete.append(doc_id)

    if ids_to_delete:
        collection.delete(ids=ids_to_delete)

    return len(ids_to_delete)
