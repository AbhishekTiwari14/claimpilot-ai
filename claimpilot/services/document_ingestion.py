import re
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from claimpilot.schemas.knowledge import Section
from claimpilot.storage.intake import IntakeError

MAX_EXTRACTED = 300_000
MAX_PAGES = 120
MAX_PDF_STREAM = 2_000_000
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HEAD = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")


def _sections(text: str, headings: bool = True) -> list[Section]:
    result = []
    title, lines = None, []
    for line in text.splitlines():
        heading = HEAD.match(line) if headings else None
        if heading:
            if "\n".join(lines).strip():
                result.append(Section(section=title, text="\n".join(lines).strip()))
            title, lines = heading.group(1)[:180], []
        else:
            lines.append(line)
    if "\n".join(lines).strip():
        result.append(Section(section=title, text="\n".join(lines).strip()))
    return result


def _pdf(data: bytes) -> list[Section]:
    from pypdf import apply_configuration

    with apply_configuration(
        jbig2dec_binary=None, zlib_maximum_output_length=MAX_PDF_STREAM,
        brotli_maximum_output_length=MAX_PDF_STREAM, lzw_maximum_output_length=MAX_PDF_STREAM,
        run_length_maximum_output_length=MAX_PDF_STREAM,
        array_based_stream_maximum_output_length=MAX_PDF_STREAM,
        stream_decoding_work_maximum_length=8_000_000, xform_maximum_invocations_per_extraction=250,
    ):
        return _pdf_sections(data)


def _pdf_sections(data: bytes) -> list[Section]:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data), root_object_recovery_limit=2_000, xref_stream_maximum_entries=100_000)
    if reader.is_encrypted:
        raise IntakeError(422, "encrypted_document", "Encrypted PDFs are unsupported.")
    if len(reader.pages) > MAX_PAGES:
        raise IntakeError(413, "document_too_large", "The document exceeds the page limit.")
    result = []
    count = 0
    for number, page in enumerate(reader.pages, 1):
        contents = page.get_contents()
        if contents and len(contents.get_data()) > MAX_PDF_STREAM:
            raise IntakeError(413, "document_too_large", "A PDF page exceeds the decoded content limit.")
        text = (page.extract_text() or "").strip()
        count += len(text)
        if count > MAX_EXTRACTED:
            raise IntakeError(413, "document_too_large", "Extracted text exceeds the limit.")
        if text:
            result.append(Section(page=number, text=text))
    if not result:
        raise IntakeError(422, "pdf_no_text", "The PDF has no extractable text. OCR is unavailable.")
    return result


def _docx(data: bytes) -> list[Section]:
    with ZipFile(BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 256 or sum(e.file_size for e in entries) > 16_000_000:
            raise IntakeError(413, "document_too_large", "DOCX expansion exceeds the limit.")
        names = {e.filename for e in entries}
        if len(names) != len(entries) or any(
            e.flag_bits & 1 or "vbaproject" in e.filename.casefold()
            or "\\" in e.filename or PurePosixPath(e.filename).is_absolute()
            or ".." in PurePosixPath(e.filename).parts for e in entries
        ):
            raise ValueError("Unsupported archive entries")
        types = archive.read("[Content_Types].xml").decode("utf-8-sig")
        if "wordprocessingml.document.main+xml" not in types or "macroenabled" in types.casefold():
            raise ValueError("Expected a DOCX content type")
        with archive.open("word/document.xml") as stream:
            xml = stream.read(2_000_001)
        if len(xml) > 2_000_000:
            raise IntakeError(413, "document_too_large", "DOCX XML exceeds the limit.")
    decoded = xml.decode("utf-8-sig")
    if "<!DOCTYPE" in decoded.upper() or "<!ENTITY" in decoded.upper():
        raise ValueError("XML declarations are unsupported")
    document = ElementTree.fromstring(decoded)
    if document.tag != WORD + "document":
        raise ValueError("Expected a Word document")
    lines = []
    for paragraph in document.iter(WORD + "p"):
        value = "".join(node.text or "" for node in paragraph.iter(WORD + "t"))
        style = paragraph.find(f"{WORD}pPr/{WORD}pStyle")
        if style is not None and style.attrib.get(WORD + "val", "").lower().startswith("heading"):
            value = "# " + value
        lines.append(value)
    return _sections("\n".join(lines))


def extract_document(filename: str, data: bytes) -> tuple[str, str, list[Section]]:
    from pypdf.errors import PyPdfError

    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 180 or any(ord(char) < 32 for char in name):
        raise IntakeError(422, "invalid_document", "Use a valid document filename.")
    kind = PurePosixPath(name).suffix.lower().removeprefix(".")
    try:
        if kind == "pdf" and data.startswith(b"%PDF-"):
            sections = _pdf(data)
        elif kind == "docx" and data.startswith(b"PK\x03\x04"):
            sections = _docx(data)
        elif kind in ("txt", "md"):
            if data.startswith((b"%PDF-", b"PK\x03\x04")):
                raise ValueError("Binary document is mislabeled as text")
            text = data.decode("utf-8-sig")
            if any(not char.isprintable() and char not in "\n\r\t" for char in text):
                raise ValueError("Expected printable UTF-8")
            sections = _sections(text, headings=kind == "md")
        else:
            raise ValueError("Format and content do not match")
    except (PyPdfError, BadZipFile, ElementTree.ParseError, ValueError, KeyError, OSError,
            TypeError, RecursionError, OverflowError, NotImplementedError):
        raise IntakeError(422, "invalid_document", "Upload a supported, readable PDF, DOCX, TXT or Markdown file.") from None
    if not sections or not any(char.isalnum() for section in sections for char in section.text):
        raise IntakeError(422, "empty_document", "The document contains no usable text.")
    if sum(len(section.text) for section in sections) > MAX_EXTRACTED or len(sections) > 500:
        raise IntakeError(413, "document_too_large", "Extracted text exceeds the limit.")
    return name, kind, sections
