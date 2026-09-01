from __future__ import annotations

import socket
import ssl
import xml.etree.ElementTree as ET
import zlib
from typing import Any

from .client_errors import EasynewsError
from .config import Settings


def decode_yenc(encoded: bytes) -> bytes:
    out = bytearray(); escaped = False
    for byte in encoded:
        if escaped:
            byte = (byte - 64) & 0xFF
            out.append((byte - 42) & 0xFF); escaped = False
        elif byte == 61:
            escaped = True
        else:
            out.append((byte - 42) & 0xFF)
    if escaped:
        raise EasynewsError("Truncated yEnc escape")
    return bytes(out)


def fetch_body(settings: Settings, username: str, password: str, message_id: str) -> bytes:
    mid = message_id.strip()
    if not mid.startswith("<"):
        mid = f"<{mid}>"
    context = ssl.create_default_context()
    try:
        with socket.create_connection((settings.nntp_host, settings.nntp_port), timeout=settings.nntp_timeout) as raw:
            with context.wrap_socket(raw, server_hostname=settings.nntp_host) as sock:
                stream = sock.makefile("rwb", buffering=0)
                def line() -> bytes:
                    data = stream.readline()
                    if not data:
                        raise EasynewsError("NNTP connection closed")
                    return data.rstrip(b"\r\n")
                greeting = line().decode("utf-8", "replace")
                if not greeting.startswith(("200", "201")):
                    raise EasynewsError(f"NNTP greeting failed: {greeting}")
                stream.write(f"AUTHINFO USER {username}\r\n".encode()); auth = line().decode("utf-8", "replace")
                if auth.startswith("381"):
                    stream.write(f"AUTHINFO PASS {password}\r\n".encode()); auth = line().decode("utf-8", "replace")
                if not auth.startswith("281"):
                    raise EasynewsError("NNTP authentication failed")
                stream.write(f"BODY {mid}\r\n".encode()); status = line().decode("utf-8", "replace")
                if not status.startswith("222"):
                    raise EasynewsError(f"NNTP article unavailable: {status}")
                body = bytearray()
                while True:
                    raw_line = line()
                    if raw_line == b".":
                        break
                    if raw_line.startswith(b".."):
                        raw_line = raw_line[1:]
                    body.extend(raw_line + b"\r\n")
                return bytes(body)
    except (OSError, ssl.SSLError) as exc:
        raise EasynewsError(f"NNTP request failed: {exc}") from exc


def parse_article(body: bytes) -> dict[str, Any]:
    ybegin: dict[str, str] = {}; ypart: dict[str, str] = {}; yend: dict[str, str] = {}
    encoded = bytearray(); active = False
    def fields(line: bytes) -> dict[str, str]:
        result = {}
        for token in line.decode("utf-8", "replace").split()[1:]:
            if "=" in token:
                key, value = token.split("=", 1); result[key.lower()] = value
        return result
    for raw in body.splitlines():
        if raw.startswith(b"=ybegin "):
            ybegin = fields(raw); active = True; continue
        if raw.startswith(b"=ypart "):
            ypart = fields(raw); continue
        if raw.startswith(b"=yend "):
            yend = fields(raw); active = False; continue
        if active:
            encoded.extend(raw)
    if not ybegin or not yend:
        raise EasynewsError("NNTP article lacks yEnc envelope")
    decoded = decode_yenc(bytes(encoded))
    expected_crc = yend.get("pcrc32") or yend.get("crc32")
    if expected_crc:
        actual = f"{zlib.crc32(decoded) & 0xFFFFFFFF:08x}"
        if actual.lower() != expected_crc.lower():
            raise EasynewsError("yEnc CRC mismatch")
    return {"decoded": decoded, "begin": ybegin, "part": ypart, "end": yend}


def validate_nzb(content: bytes) -> bytes:
    content = content.replace(b'date=""', b'date="0"')
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise EasynewsError(f"Content is not valid NZB XML: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "nzb":
        raise EasynewsError("XML root is not <nzb>")
    return content


def download_posted_nzb(settings: Settings, username: str, password: str, message_id: str,
                        sibling_message_ids: list[str] | None = None) -> bytes:
    mids = sibling_message_ids or [message_id]
    parts: list[tuple[int, bytes]] = []; total_expected = None
    for mid in mids:
        parsed = parse_article(fetch_body(settings, username, password, mid))
        decoded = parsed["decoded"]; begin = parsed["begin"]; part = parsed["part"]
        try:
            total = int(begin.get("total", "1")); part_no = int(begin.get("part", "1"))
        except ValueError:
            total, part_no = 1, 1
        total_expected = total if total_expected is None else total_expected
        try:
            order = int(part.get("begin") or part_no)
        except ValueError:
            order = part_no
        parts.append((order, decoded))
    if total_expected and total_expected > len(parts):
        raise EasynewsError(f"Multipart NZB incomplete: need {total_expected} articles, have {len(parts)}")
    return validate_nzb(b"".join(data for _, data in sorted(parts, key=lambda x: x[0])))


def inspect_posted_nzb(settings: Settings, username: str, password: str, message_id: str,
                       sibling_message_ids: list[str] | None = None) -> dict[str, Any]:
    content = download_posted_nzb(settings, username, password, message_id, sibling_message_ids)
    root = ET.fromstring(content)
    files = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() == "file"]
    segments = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() == "segment"]
    total_bytes = 0
    for segment in segments:
        try:
            total_bytes += int(segment.attrib.get("bytes", "0"))
        except (TypeError, ValueError):
            pass
    return {"content": content, "size": total_bytes, "file_count": len(files), "segment_count": len(segments)}
