from __future__ import annotations

import hashlib
import csv
import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from backend.core import storage


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
MAX_FILE_BYTES = 500_000
MAX_TOOL_CONTEXT_CHARS = 12_000
COMMAND_TIMEOUT_SECONDS = 30
APPROVAL_TTL_SECONDS = 300
SUPPORTED_FILE_EXTENSIONS = "pdf|txt|md|csv|json|py|js|ts|html|css"
QUOTED_PATH_RE = re.compile(
    rf"[\"'](?P<path>[^\"']+?\.(?:{SUPPORTED_FILE_EXTENSIONS}))[\"']",
    re.IGNORECASE,
)
BARE_PATH_RE = re.compile(
    rf"(?P<path>(?:[A-Za-z]:)?[^\s\"'<>]+?\.(?:{SUPPORTED_FILE_EXTENSIONS}))",
    re.IGNORECASE,
)
_pending_approvals: dict[str, tuple[float, "AgentToolCall"]] = {}
_approval_lock = threading.Lock()


class AgentToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentToolCall:
    id: str
    name: str
    arguments: dict[str, str]
    requires_confirmation: bool = False


@dataclass(frozen=True)
class AgentToolResult:
    name: str
    status: str
    summary: str
    content: str = ""
    requires_confirmation: bool = False
    call: AgentToolCall | None = None


def local_memories_context() -> str:
    memories = storage.list_memories(limit=20)
    if not memories:
        return ""
    lines = ["Local memory:"]
    for memory in memories:
        lines.append(f"- {memory['content']}")
    return "\n".join(lines)


def maybe_store_memory(prompt: str) -> AgentToolResult | None:
    normalized = prompt.strip()
    match = re.search(r"\bremember(?: that)?\b[:\s]+(.+)", normalized, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    content = match.group(1).strip()
    if len(content) < 3:
        return None
    memory = storage.create_memory(content=content[:1_000], source="user")
    return AgentToolResult(
        name="memory.write",
        status="completed",
        summary=f"Saved memory: {memory['content']}",
        content=memory["content"],
    )


def detect_file_read(prompt: str) -> AgentToolCall | None:
    if not re.search(r"\b(read|summarize|open|inspect|analyze)\b", prompt, re.IGNORECASE):
        return None
    match = QUOTED_PATH_RE.search(prompt) or BARE_PATH_RE.search(prompt)
    if not match:
        return None
    path = match.group("path").strip().strip("\"'")
    return build_call("file.read", {"path": path})


def detect_command(prompt: str) -> AgentToolCall | None:
    patterns = (
        r"\brun command\s*[:\-]\s*(.+)",
        r"\brun\s*[:\-]\s*(.+)",
        r"\bexecute\s*[:\-]\s*(.+)",
    )
    for pattern in patterns:
        match = re.search(pattern, prompt, re.IGNORECASE | re.DOTALL)
        if match:
            command = match.group(1).strip()
            if command:
                return build_call("command.run", {"command": command}, requires_confirmation=True)
    return None


def execute_tool_call(call: AgentToolCall) -> AgentToolResult:
    if call.name == "file.read":
        return read_local_file(call.arguments.get("path", ""))
    if call.name == "file.write":
        return write_local_file(
            call.arguments.get("path", ""),
            call.arguments.get("content", ""),
            call.arguments.get("mode", "replace"),
        )
    if call.name == "command.run":
        return run_command(call.arguments.get("command", ""))
    raise AgentToolError(f"Unknown tool: {call.name}")


def register_pending_approval(call: AgentToolCall) -> AgentToolCall:
    if not call.requires_confirmation:
        raise AgentToolError("Only confirmation-gated tools can be registered for approval.")
    with _approval_lock:
        prune_expired_approvals()
        _pending_approvals[call.id] = (time.monotonic() + APPROVAL_TTL_SECONDS, call)
    return call


def consume_pending_approval(call_id: str) -> AgentToolCall:
    with _approval_lock:
        prune_expired_approvals()
        entry = _pending_approvals.pop(call_id, None)
    if entry is None:
        raise AgentToolError("This tool approval is missing, expired, or already used.")
    return entry[1]


def prune_expired_approvals() -> None:
    now = time.monotonic()
    expired = [call_id for call_id, (expires_at, _) in _pending_approvals.items() if expires_at <= now]
    for call_id in expired:
        _pending_approvals.pop(call_id, None)


def read_local_file(raw_path: str) -> AgentToolResult:
    path = resolve_workspace_path(raw_path)
    if not path.exists() or not path.is_file():
        raise AgentToolError(f"File not found: {raw_path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise AgentToolError("That file is too large for the current local reader.")

    suffix = path.suffix.lower()
    relative = path.relative_to(WORKSPACE_ROOT)
    if suffix == ".pdf":
        content = read_pdf_text(path)
        summary = f"Extracted text from PDF: {relative}"
    elif suffix == ".csv":
        content = read_csv_summary(path)
        summary = f"Read CSV data: {relative}"
    elif suffix == ".json":
        content = read_json_summary(path)
        summary = f"Read JSON data: {relative}"
    else:
        content = path.read_text(encoding="utf-8", errors="replace")
        summary = f"Read local file: {relative}"

    return AgentToolResult(
        name="file.read",
        status="completed",
        summary=summary,
        content=content[:MAX_TOOL_CONTEXT_CHARS],
    )


def read_csv_summary(path: Path) -> str:
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.reader(StringIO(content))
    rows = list(reader)
    if not rows:
        return "CSV file is empty."

    headers = rows[0]
    data_rows = rows[1:]
    preview_rows = data_rows[:20]
    lines = [
        f"CSV summary: {len(data_rows)} data rows, {len(headers)} columns.",
        f"Columns: {', '.join(headers)}",
        "",
        "Preview:",
        ",".join(headers),
    ]
    lines.extend(",".join(row) for row in preview_rows)
    if len(data_rows) > len(preview_rows):
        lines.append(f"... {len(data_rows) - len(preview_rows)} more rows")
    return "\n".join(lines)


def read_json_summary(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise AgentToolError(
            f"Invalid JSON near line {exc.lineno}, column {exc.colno}."
        ) from exc
    shape = describe_json_shape(data)
    pretty = json.dumps(data, indent=2, ensure_ascii=False)
    return f"JSON summary: {shape}\n\n{pretty[:MAX_TOOL_CONTEXT_CHARS]}"


def describe_json_shape(value: object) -> str:
    if isinstance(value, dict):
        keys = list(value.keys())
        return f"object with {len(keys)} keys: {', '.join(map(str, keys[:12]))}"
    if isinstance(value, list):
        item_type = type(value[0]).__name__ if value else "empty"
        return f"array with {len(value)} items; first item type: {item_type}"
    return type(value).__name__


def read_pdf_text(path: Path) -> str:
    try:
        reader = PdfReader(path)
        text = normalize_tool_text("\n\n".join(page.extract_text() or "" for page in reader.pages))
    except Exception as exc:
        raise AgentToolError(f"Could not read PDF: {exc}") from exc
    if not text:
        raise AgentToolError("No readable PDF text was found. Scanned PDFs need an OCR step.")
    return text


def decode_pdf_text_chunk(chunk: bytes) -> str:
    chunk = re.sub(rb"\\[nrtbf]", b" ", chunk)
    chunk = re.sub(rb"\\([()\\])", rb"\1", chunk)
    chunk = re.sub(rb"<[0-9A-Fa-f]+>", b" ", chunk)
    chunk = re.sub(rb"[-+]?\d+(?:\.\d+)?", b" ", chunk)
    return chunk.decode("latin-1", errors="ignore")


def normalize_tool_text(value: str) -> str:
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\s*\n\s*", "\n", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip()


def write_local_file(raw_path: str, content: str, mode: str = "replace") -> AgentToolResult:
    path = resolve_workspace_path(raw_path)
    suffix = path.suffix.lower()
    if suffix not in {".csv", ".json", ".pdf"}:
        raise AgentToolError("File editing currently supports CSV, JSON, and PDF files.")
    if not content.strip():
        raise AgentToolError("No file content was provided.")

    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".csv":
        write_csv_file(path, content, mode)
    elif suffix == ".json":
        write_json_file(path, content, mode)
    else:
        write_pdf_file(path, content, mode)

    relative = path.relative_to(WORKSPACE_ROOT)
    return AgentToolResult(
        name="file.write",
        status="completed",
        summary=f"Updated local file: {relative} ({mode})",
        content=f"Saved {relative}",
    )


def write_csv_file(path: Path, content: str, mode: str) -> None:
    if mode not in {"replace", "append"}:
        raise AgentToolError("CSV mode must be replace or append.")
    candidate = content.strip()
    if mode == "append" and path.exists() and path.stat().st_size:
        existing = path.read_text(encoding="utf-8-sig", errors="replace").rstrip()
        candidate = f"{existing}\n{candidate}"
    rows = list(csv.reader(StringIO(candidate)))
    if not rows or not all(rows):
        raise AgentToolError("CSV content is empty or malformed.")
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise AgentToolError("CSV rows must all have the same number of columns.")
    atomic_write_text(path, candidate + "\n")


def write_json_file(path: Path, content: str, mode: str) -> None:
    if mode not in {"replace", "merge"}:
        raise AgentToolError("JSON mode must be replace or merge.")
    try:
        incoming = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AgentToolError(f"Invalid JSON near line {exc.lineno}, column {exc.colno}.") from exc
    if mode == "merge":
        if not isinstance(incoming, dict):
            raise AgentToolError("JSON merge content must be an object.")
        current: object = {}
        if path.exists() and path.stat().st_size:
            try:
                current = json.loads(path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                raise AgentToolError("The existing JSON file is invalid and cannot be merged.") from exc
        if not isinstance(current, dict):
            raise AgentToolError("The existing JSON value must be an object for merge mode.")
        incoming = {**current, **incoming}
    atomic_write_text(path, json.dumps(incoming, indent=2, ensure_ascii=False) + "\n")


def write_pdf_file(path: Path, content: str, mode: str) -> None:
    if mode not in {"replace", "append"}:
        raise AgentToolError("PDF mode must be replace or append.")
    generated = create_text_pdf(content)
    writer = PdfWriter()
    if mode == "append" and path.exists() and path.stat().st_size:
        try:
            existing = PdfReader(path)
            for page in existing.pages:
                writer.add_page(page)
        except Exception as exc:
            raise AgentToolError(f"Could not open the existing PDF: {exc}") from exc
    for page in PdfReader(generated).pages:
        writer.add_page(page)
    temporary = path.with_name(f".{path.name}.stillgaze.tmp")
    try:
        with temporary.open("wb") as file:
            writer.write(file)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def create_text_pdf(content: str) -> BytesIO:
    buffer = BytesIO()
    document = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    text = document.beginText(54, height - 54)
    text.setFont("Helvetica", 11)
    for paragraph in content.splitlines() or [content]:
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if document.stringWidth(candidate, "Helvetica", 11) > width - 108:
                text.textLine(line)
                line = word
            else:
                line = candidate
            if text.getY() < 54:
                document.drawText(text)
                document.showPage()
                text = document.beginText(54, height - 54)
                text.setFont("Helvetica", 11)
        text.textLine(line)
    document.drawText(text)
    document.save()
    buffer.seek(0)
    return buffer


def atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.stillgaze.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run_command(command: str) -> AgentToolResult:
    if not command.strip():
        raise AgentToolError("No command was provided.")
    try:
        completed = subprocess.run(
            command,
            cwd=WORKSPACE_ROOT,
            shell=True,
            text=True,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise AgentToolError(
            f"Command timed out after {COMMAND_TIMEOUT_SECONDS} seconds."
        ) from exc
    output = "\n".join(
        part for part in [completed.stdout.strip(), completed.stderr.strip()] if part
    ).strip()
    summary = f"Command exited with code {completed.returncode}: {command}"
    return AgentToolResult(
        name="command.run",
        status="completed" if completed.returncode == 0 else "error",
        summary=summary,
        content=output[:MAX_TOOL_CONTEXT_CHARS] or "(no output)",
    )


def resolve_workspace_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = WORKSPACE_ROOT / candidate
    resolved = candidate.resolve()
    if WORKSPACE_ROOT not in resolved.parents and resolved != WORKSPACE_ROOT:
        raise AgentToolError("StillGaze can only read files inside this project folder for now.")
    return resolved


def build_call(name: str, arguments: dict[str, str], requires_confirmation: bool = False) -> AgentToolCall:
    payload = json.dumps({"name": name, "arguments": arguments}, sort_keys=True)
    call_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return AgentToolCall(
        id=call_id,
        name=name,
        arguments=arguments,
        requires_confirmation=requires_confirmation,
    )
