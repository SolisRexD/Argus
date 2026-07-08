import binascii
import struct
import zlib

from argus_core.capture.png import force_png_alpha_opaque


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _write_rgba_png(path, pixels, width, height):
    rows = []
    for y in range(height):
        start = y * width * 4
        end = start + width * 4
        rows.append(b"\x00" + bytes(pixels[start:end]))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + _chunk(b"IEND", b"")
    )


def _read_rgba_png(path):
    data = path.read_bytes()
    pos = len(PNG_SIGNATURE)
    width = height = None
    compressed = []

    while pos < len(data):
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + size]
        pos += 12 + size

        if kind == b"IHDR":
            width, height, bit_depth, color_type, *_ = struct.unpack(">IIBBBBB", payload)
            assert bit_depth == 8
            assert color_type == 6
        elif kind == b"IDAT":
            compressed.append(payload)
        elif kind == b"IEND":
            break

    raw = zlib.decompress(b"".join(compressed))
    stride = width * 4
    pixels = []
    for y in range(height):
        row = raw[y * (stride + 1) : (y + 1) * (stride + 1)]
        assert row[0] == 0
        pixels.extend(row[1:])

    return pixels


def test_force_png_alpha_opaque_preserves_rgb_and_sets_alpha_to_255(tmp_path):
    path = tmp_path / "mask.png"
    pixels = [
        255,
        0,
        255,
        0,
        255,
        0,
        0,
        17,
    ]
    _write_rgba_png(path, pixels, width=2, height=1)

    assert force_png_alpha_opaque(path) is True

    rewritten = _read_rgba_png(path)
    assert rewritten == [
        255,
        0,
        255,
        255,
        255,
        0,
        0,
        255,
    ]
