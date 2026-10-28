"""Synthetic fixtures for extraction tests. Nothing here is a real document: PDFs are built from
plain text lines, images are drawn from a tiny bitmap font, and OCR can be replaced by a fake."""

import struct
import zlib
from dataclasses import dataclass, field

from app.extraction.ocr import OcrError

STANDARD_LINES = [
    "State Bank of India",
    "Fixed Deposit Receipt",
    "Branch: Pune Camp",
    "FDR No: 12345678901",
    "Depositor: PICT Test Institute",
    "Principal Amount: Rs. 15,00,000.00",
    "Date of Deposit: 15 Jan 2026",
    "Maturity Date: 15 Jan 2027",
    "Rate of Interest: 7.10 % p.a.",
]


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _assemble(objects: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_pdf(pages: list[list[str]]) -> bytes:
    """A PDF whose pages carry the given lines as embedded (selectable) text."""
    objects: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        content = "BT /F1 12 Tf 14 TL 50 750 Td " + " ".join(f"({_escape(line)}) Tj T*" for line in lines) + " ET"
        stream = content.encode("latin-1")
        page_no = len(objects) + 1
        kids.append(f"{page_no} 0 R")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {page_no + 1} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    return _assemble(objects)


def make_unicode_pdf(pages: list[list[str]]) -> bytes:
    """A PDF whose pages carry the given lines as embedded text in ANY script. Text is written with
    one-byte codes and a /ToUnicode map, which is how real PDFs expose Devanagari to text extraction."""
    chars = sorted({ch for lines in pages for line in lines for ch in line})
    assert len(chars) < 250, "too many distinct characters for a one-byte synthetic font"
    code_of = {ch: i + 1 for i, ch in enumerate(chars)}

    def utf16_hex(ch: str) -> str:
        return ch.encode("utf-16-be").hex().upper()

    pairs = "\n".join(f"<{code:02X}> <{utf16_hex(ch)}>" for ch, code in code_of.items())
    cmap = (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Adobe-Identity-UCS def /CMapType 2 def "
        "1 begincodespacerange <00> <FF> endcodespacerange "
        f"{len(code_of)} beginbfchar\n{pairs}\nendbfchar endcmap CMapName currentdict /CMap defineresource pop end end"
    ).encode("ascii")
    last = len(chars)
    objects: list[bytes] = [
        b"", b"",
        (f"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /FirstChar 1 /LastChar {last} "
         f"/Widths [{' '.join(['500'] * last)}] /ToUnicode 4 0 R >>").encode(),
        f"<< /Length {len(cmap)} >>\nstream\n".encode() + cmap + b"\nendstream",
    ]
    kids = []
    for lines in pages:
        content = "BT /F1 12 Tf 14 TL 50 750 Td " + " ".join(
            "<" + "".join(f"{code_of[ch]:02X}" for ch in line) + "> Tj T*" for line in lines) + " ET"
        stream = content.encode("ascii")
        page_no = len(objects) + 1
        kids.append(f"{page_no} 0 R")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {page_no + 1} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    return _assemble(objects)


def make_receipt_pdf(**replacements: str | None) -> bytes:
    """The standard synthetic receipt; a replacement of None removes that line."""
    lines = []
    for line in STANDARD_LINES:
        label = line.split(":")[0]
        if label in replacements:
            if replacements[label] is None:
                continue
            line = f"{label}: {replacements[label]}"
        lines.append(line)
    return make_pdf([lines])


def tiny_jpeg(width: int = 64, height: int = 64) -> bytes:
    """A header-valid JPEG stub (right signature and dimensions; not meant to be decoded)."""
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof + b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x00\xff\xd9"


def make_image_pdf(jpeg: bytes | None = None, width: int = 64, height: int = 64) -> bytes:
    """A PDF page that is only a picture (a 'scanned' page): no text layer."""
    jpeg = jpeg or tiny_jpeg(width, height)
    content = b"q 400 0 0 400 50 300 cm /Im0 Do Q"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
        (f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceGray "
         f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n").encode() + jpeg + b"\nendstream",
    ]
    return _assemble(objects)


# ---- a tiny raster font, so a real OCR engine can be smoke-tested without an imaging library -----
_GLYPHS = {
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11110 00001 00001 01110 00001 00001 11110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    "F": "11111 10000 10000 11110 10000 10000 10000", "D": "11110 10001 10001 10001 10001 10001 11110",
    "R": "11110 10001 10001 11110 10100 10010 10001", "N": "10001 11001 10101 10011 10001 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", " ": "00000 00000 00000 00000 00000 00000 00000",
}


def bitmap_text_png(text: str, scale: int = 10) -> bytes:
    rows = []
    margin = 5 * scale
    width = margin * 2 + len(text) * 6 * scale
    height = margin * 2 + 7 * scale
    canvas = [[255] * width for _ in range(height)]
    for index, char in enumerate(text):
        for y, row in enumerate(_GLYPHS[char].split()):
            for x, bit in enumerate(row):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            canvas[margin + y * scale + dy][margin + (index * 6 + x) * scale + dx] = 0
    raw = b"".join(b"\x00" + bytes(line) for line in canvas)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def png_with_size(width: int, height: int) -> bytes:
    """A header-valid PNG stub claiming the given size (for pixel-limit tests; not decodable)."""
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
            + b"\x00\x00\x00\x00")


@dataclass
class FakeOcr:
    """An OCR engine that returns canned text (or fails) so tests control exactly what 'was read'."""

    text: str = ""
    fail: str | None = None  # "failed" | "timeout"
    is_available: bool = True
    name: str = "fake-ocr"
    calls: list[str] = field(default_factory=list)

    def available(self) -> bool:
        return self.is_available

    def recognize(self, image: bytes, suffix: str, timeout: float) -> str:
        self.calls.append(suffix)
        if self.fail:
            raise OcrError(self.fail)
        return self.text
