"""The golden vectors (``vectors/fidu1/``), and every single-field mutation of them.

The vectors are frozen: the generator's output must equal the files. Each
valid document reads, re-encodes to the same bytes, digests and signs as
recorded and verifies under its key; each invalid one is refused with the
error recorded for it. Then every valid document is mutated one field at a
time (a field dropped, an unknown one added, one retyped, a float put in, a
set reordered) and every mutation must be refused (F3-1).
"""

from __future__ import annotations

import copy
import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fiduciary import documents, fidu1
from fiduciary.documents import TYPES, Record
from fiduciary.exceptions import FiduciaryError, MalformedError, SignatureError

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "vectors" / "fidu1"


def _load(name: str) -> Any:
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


def test_the_vectors_are_frozen() -> None:
    spec = importlib.util.spec_from_file_location(
        "make_vectors", ROOT / "scripts" / "make_vectors.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, text in module.build().items():
        assert (VECTORS / name).read_text(encoding="utf-8") == text, (
            f"{name} differs from its generator"
        )


@pytest.mark.parametrize("vector", _load("valid.json"), ids=lambda v: v["name"])
def test_a_valid_vector_reads_digests_and_verifies(vector: dict[str, Any]) -> None:
    document = documents.load(vector["document"])
    assert document.TYPE == vector["type"]
    assert document.to_json() == vector["document"]
    assert document.digest == vector["canonical_sha256"]
    assert document.signing_input().hex() == vector["signing_input"]
    assert len(document.signing_input()) <= fidu1.MAX_SIGNING_INPUT
    document.verify(fidu1.key_of(vector["key"]))


def test_the_record_vectors_chain_and_verify() -> None:
    found = _load("records.json")
    key = fidu1.key_of(found["key"])
    prev = fidu1.GENESIS
    for entry in found["records"]:
        record = Record.from_json(entry["record"])
        record.verify(key)
        assert record.prev == prev and record.hash == entry["hash"]
        assert record.signing_input().hex() == entry["signing_input"]
        prev = record.hash


@pytest.mark.parametrize("vector", _load("invalid.json"), ids=lambda v: v["name"])
def test_an_invalid_vector_is_refused_with_its_error(vector: dict[str, Any]) -> None:
    kind, _, code = vector["error"].partition(":")
    cls = TYPES[vector["type"]]
    with pytest.raises(FiduciaryError) as caught:
        if "text" in vector:
            cls.loads(vector["text"], signed=vector["signed"])
        else:
            document = cls.from_json(vector["document"], signed=vector["signed"])
            document.verify(fidu1.key_of(vector["key"]))
    error = caught.value
    if kind == "malformed":
        assert isinstance(error, MalformedError), error
        assert error.code == code, error
        assert error.path.startswith(vector.get("path", "$")), error
    else:
        assert isinstance(error, SignatureError), error
        assert error.code == code, error


def test_every_malformed_code_has_a_vector() -> None:
    from fiduciary.exceptions import MALFORMED_CODES

    found = {
        v["error"].split(":")[1]
        for v in _load("invalid.json")
        if v["error"].startswith("malformed:")
    }
    assert found == set(MALFORMED_CODES)


# -- the mutation sweep (F3-1) ------------------------------------------------------

FREE_FORM = ("$.input", "$.body", ".payload")
"""Subtrees whose keys are the author's: a tool's JSON Schema, a record's body,
a request's payload. Their own rules are tested apart."""
PAIRS = (".key",)
DATA_MAPS = (
    ".tables",
    ".sinks",
    ".per_sink",
    ".maximum",
    ".tenant_domains",
    "$.outbound.tenants.fields",
    ".parameters",
    ".when",
)
"""Objects whose keys are data, not fields: dropping an entry is another valid
mandate, narrower, not a malformed one. An unknown entry is still refused."""
"""Arrays whose order means something and is not a set: a join's two columns."""


def _paths(value: Any, path: str = "$") -> Iterator[tuple[str, Any]]:
    yield path, value
    if any(path.endswith(f) or f"{f}." in path or f"{f}[" in path for f in FREE_FORM):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _paths(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _paths(item, f"{path}[{index}]")


def _get_parent(doc: Any, path: str) -> tuple[Any, str | int]:
    parts: list[str | int] = []
    for token in path[2:].replace("[", ".[").split("."):
        if not token:
            continue
        parts.append(int(token[1:-1]) if token.startswith("[") else token)
    target = doc
    for part in parts[:-1]:
        target = target[part]
    return target, parts[-1]


def _retyped(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, bool):
        return "true"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return 12345
    if isinstance(value, list):
        return {}
    return []


def mutations(document: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for path, value in _paths(document):
        if path == "$":
            continue
        free = any(f in path for f in FREE_FORM)
        parent, _ = _get_parent(document, path)
        if path.startswith("$.sig"):
            continue  # a signature's own fields are checked by the signature vectors
        parent_path = path.rsplit(".", 1)[0]
        if isinstance(parent, dict) and not free and not parent_path.endswith(DATA_MAPS):
            dropped = copy.deepcopy(document)
            target, last = _get_parent(dropped, path)
            del target[last]
            yield f"drop {path}", dropped
        retyped = copy.deepcopy(document)
        target, last = _get_parent(retyped, path)
        target[last] = _retyped(value)
        yield f"retype {path}", retyped
        if isinstance(value, int) and not isinstance(value, bool):
            floated = copy.deepcopy(document)
            target, last = _get_parent(floated, path)
            target[last] = value + 0.5
            yield f"float {path}", floated
        if isinstance(value, dict) and not free and not path.endswith(PAIRS):
            added = copy.deepcopy(document)
            target, last = _get_parent(added, path)
            target[last]["zz_unknown"] = {"\u0000": 1}
            yield f"add to {path}", added
        if (
            isinstance(value, list)
            and len(value) >= 2
            and value[0] != value[1]
            and not path.endswith(PAIRS)
            and not free
        ):
            if any(path.endswith(s) for s in (".effects", ".requests")):
                continue  # steps in order; their own rules refuse a reordering that breaks them
            swapped = copy.deepcopy(document)
            target, last = _get_parent(swapped, path)
            items = target[last]
            items[0], items[1] = items[1], items[0]
            yield f"reorder {path}", swapped
    top = copy.deepcopy(document)
    top["zz_unknown"] = 1
    yield "add to $", top


@pytest.mark.parametrize("vector", _load("valid.json"), ids=lambda v: v["name"])
def test_every_single_field_mutation_is_refused(vector: dict[str, Any]) -> None:
    cls = TYPES[vector["type"]]
    count = 0
    for name, mutated in mutations(vector["document"]):
        count += 1
        try:
            cls.from_json(mutated)
        except MalformedError:
            continue
        pytest.fail(f"{vector['name']}: the mutation '{name}' was read as a valid {cls.TYPE}")
    assert count >= 10


@pytest.mark.parametrize("vector", _load("valid.json"), ids=lambda v: v["name"])
def test_every_change_to_a_signed_document_breaks_its_signature(vector: dict[str, Any]) -> None:
    """A change that leaves a document well formed still does not verify."""
    cls = TYPES[vector["type"]]
    key = fidu1.key_of(vector["key"])
    changed = 0
    for path, value in _paths(vector["document"]):
        if path.startswith("$.sig") or not isinstance(value, bool):
            continue
        flipped = copy.deepcopy(vector["document"])
        target, last = _get_parent(flipped, path)
        target[last] = not value
        try:
            document = cls.from_json(flipped)
        except MalformedError:
            continue
        with pytest.raises(SignatureError):
            document.verify(key)
        changed += 1
    label = copy.deepcopy(vector["document"])
    for field in ("description", "label"):
        if field in label:
            label[field] += "."
    if "subject" in label and isinstance(label["subject"], dict) and "label" in label["subject"]:
        label["subject"]["label"] += "."
    if label != vector["document"]:
        with pytest.raises(SignatureError):
            cls.from_json(label).verify(key)
