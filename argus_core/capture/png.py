"""PNG post-processing helpers for exported capture files."""

import binascii
import struct
import zlib


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def force_png_alpha_opaque(path):
    """Set alpha to 255 for 8-bit RGBA PNG files using only stdlib."""
    path = str(path)
    data = _read_file(path)

    if not data.startswith(PNG_SIGNATURE):
        return False

    chunks = _read_chunks(data)
    ihdr = _find_first(chunks, b"IHDR")

    if not ihdr:
        return False

    width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB",
        ihdr,
    )

    if (bit_depth, color_type, compression, filter_method, interlace) != (8, 6, 0, 0, 0):
        return False

    compressed = b"".join(payload for kind, payload in chunks if kind == b"IDAT")
    raw = zlib.decompress(compressed)
    rgba_rows = _unfilter_rows(raw, width, height, bytes_per_pixel=4)

    changed = False
    for row in rgba_rows:
        for alpha_index in range(3, len(row), 4):
            if row[alpha_index] != 255:
                row[alpha_index] = 255
                changed = True

    if not changed:
        return True

    filtered = b"".join(b"\x00" + bytes(row) for row in rgba_rows)
    idat = zlib.compress(filtered)

    rebuilt = bytearray(PNG_SIGNATURE)
    idat_written = False
    for kind, payload in chunks:
        if kind == b"IDAT":
            if not idat_written:
                rebuilt.extend(_make_chunk(b"IDAT", idat))
                idat_written = True
            continue

        rebuilt.extend(_make_chunk(kind, payload))

    _write_file(path, bytes(rebuilt))
    return True


def _read_file(path):
    with open(path, "rb") as f:
        return f.read()


def _write_file(path, data):
    with open(path, "wb") as f:
        f.write(data)


def _read_chunks(data):
    chunks = []
    pos = len(PNG_SIGNATURE)

    while pos + 8 <= len(data):
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        payload_start = pos + 8
        payload_end = payload_start + size
        payload = data[payload_start:payload_end]
        chunks.append((kind, payload))
        pos = payload_end + 4

        if kind == b"IEND":
            break

    return chunks


def _find_first(chunks, target_kind):
    for kind, payload in chunks:
        if kind == target_kind:
            return payload

    return None


def _make_chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _unfilter_rows(raw, width, height, bytes_per_pixel):
    stride = width * bytes_per_pixel
    rows = []
    pos = 0
    previous = bytearray(stride)

    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        current = bytearray(raw[pos : pos + stride])
        pos += stride

        if filter_type == 0:
            pass
        elif filter_type == 1:
            for i in range(stride):
                left = current[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                current[i] = (current[i] + left) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                current[i] = (current[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = current[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                up = previous[i]
                current[i] = (current[i] + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = current[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                up = previous[i]
                up_left = previous[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                current[i] = (current[i] + _paeth(left, up, up_left)) & 0xFF
        else:
            raise ValueError("Unsupported PNG filter type: {}".format(filter_type))

        rows.append(current)
        previous = current

    return rows


def _paeth(left, up, up_left):
    estimate = left + up - up_left
    pa = abs(estimate - left)
    pb = abs(estimate - up)
    pc = abs(estimate - up_left)

    if pa <= pb and pa <= pc:
        return left

    if pb <= pc:
        return up

    return up_left
