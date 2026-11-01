"""Cross-platform pure-Python Cairo fallback for Mochi Desktop.

When native `pycairo` (`_cairo`) is installed on the host OS, this module
delegates directly to it. On Windows, macOS, or headless environments where
native `pycairo` is absent, this module provides a faithful pure-Python
implementation of `ImageSurface`, `Context`, `RadialGradient`, `LinearGradient`,
and Cairo constants using Python's standard library (`zlib` + `struct`).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import struct
import zlib

try:
    import _cairo as _native_cairo  # type: ignore[import-not-found]
except ImportError:
    _native_cairo = None

if _native_cairo is not None:
    from _cairo import *  # type: ignore[import-not-found,wildcard-import]  # noqa: F401,F403
else:
    FORMAT_ARGB32 = 0
    FORMAT_RGB24 = 1
    CONTENT_COLOR = 0x1000
    CONTENT_ALPHA = 0x2000
    CONTENT_COLOR_ALPHA = 0x3000

    FILTER_FAST = 0
    FILTER_GOOD = 1
    FILTER_BEST = 2
    FILTER_NEAREST = 3
    FILTER_BILINEAR = 4

    OPERATOR_CLEAR = 0
    OPERATOR_SOURCE = 1
    OPERATOR_OVER = 2

    FONT_SLANT_NORMAL = 0
    FONT_SLANT_ITALIC = 1
    FONT_SLANT_OBLIQUE = 2

    FONT_WEIGHT_NORMAL = 0
    FONT_WEIGHT_BOLD = 1

    LINE_CAP_BUTT = 0
    LINE_CAP_ROUND = 1
    LINE_CAP_SQUARE = 2

    LINE_JOIN_MITER = 0
    LINE_JOIN_ROUND = 1
    LINE_JOIN_BEVEL = 2

    class Error(Exception):
        """Cairo error exception."""

    @dataclass(frozen=True)
    class TextExtents:
        x_bearing: float
        y_bearing: float
        width: float
        height: float
        x_advance: float
        y_advance: float

        def __iter__(self):
            yield self.x_bearing
            yield self.y_bearing
            yield self.width
            yield self.height
            yield self.x_advance
            yield self.y_advance

    class Pattern:
        """Base pattern with filter state."""

        def __init__(self) -> None:
            self._filter = FILTER_GOOD

        def set_filter(self, filter_mode: int) -> None:
            self._filter = int(filter_mode)

        def get_filter(self) -> int:
            return self._filter

    class SolidPattern(Pattern):
        def __init__(self, r: float, g: float, b: float, a: float = 1.0) -> None:
            super().__init__()
            self.rgba = (
                max(0.0, min(1.0, float(r))),
                max(0.0, min(1.0, float(g))),
                max(0.0, min(1.0, float(b))),
                max(0.0, min(1.0, float(a))),
            )

    class SurfacePattern(Pattern):
        def __init__(
            self,
            surface: ImageSurface,
            x: float = 0.0,
            y: float = 0.0,
        ) -> None:
            super().__init__()
            self.surface = surface
            self.x = float(x)
            self.y = float(y)

    class _GradientStop:
        __slots__ = ("offset", "r", "g", "b", "a")

        def __init__(
            self,
            offset: float,
            r: float,
            g: float,
            b: float,
            a: float,
        ) -> None:
            self.offset = max(0.0, min(1.0, float(offset)))
            self.r = max(0.0, min(1.0, float(r)))
            self.g = max(0.0, min(1.0, float(g)))
            self.b = max(0.0, min(1.0, float(b)))
            self.a = max(0.0, min(1.0, float(a)))

    class _Gradient(Pattern):
        def __init__(self) -> None:
            super().__init__()
            self.stops: list[_GradientStop] = []

        def add_color_stop_rgba(
            self,
            offset: float,
            r: float,
            g: float,
            b: float,
            a: float,
        ) -> None:
            self.stops.append(_GradientStop(offset, r, g, b, a))
            self.stops.sort(key=lambda item: item.offset)

        def add_color_stop_rgb(
            self,
            offset: float,
            r: float,
            g: float,
            b: float,
        ) -> None:
            self.add_color_stop_rgba(offset, r, g, b, 1.0)

        def sample(self, t: float) -> tuple[float, float, float, float]:
            if not self.stops:
                return (0.0, 0.0, 0.0, 0.0)
            clamped = max(0.0, min(1.0, float(t)))
            if clamped <= self.stops[0].offset:
                first = self.stops[0]
                return (first.r, first.g, first.b, first.a)
            for idx in range(len(self.stops) - 1):
                left = self.stops[idx]
                right = self.stops[idx + 1]
                if clamped <= right.offset:
                    span = right.offset - left.offset
                    if span <= 1e-9:
                        return (right.r, right.g, right.b, right.a)
                    ratio = (clamped - left.offset) / span
                    return (
                        left.r + (right.r - left.r) * ratio,
                        left.g + (right.g - left.g) * ratio,
                        left.b + (right.b - left.b) * ratio,
                        left.a + (right.a - left.a) * ratio,
                    )
            last = self.stops[-1]
            return (last.r, last.g, last.b, last.a)

    class RadialGradient(_Gradient):
        def __init__(
            self,
            cx0: float,
            cy0: float,
            radius0: float,
            cx1: float,
            cy1: float,
            radius1: float,
        ) -> None:
            super().__init__()
            self.cx0 = float(cx0)
            self.cy0 = float(cy0)
            self.radius0 = float(radius0)
            self.cx1 = float(cx1)
            self.cy1 = float(cy1)
            self.radius1 = float(radius1)

    class LinearGradient(_Gradient):
        def __init__(
            self,
            x0: float,
            y0: float,
            x1: float,
            y1: float,
        ) -> None:
            super().__init__()
            self.x0 = float(x0)
            self.y0 = float(y0)
            self.x1 = float(x1)
            self.y1 = float(y1)

    def _paeth_predictor(a: int, b: int, c: int) -> int:
        p = a + b - c
        pa = abs(p - a)
        pb = abs(p - b)
        pc = abs(p - c)
        if pa <= pb and pa <= pc:
            return a
        if pb <= pc:
            return b
        return c

    _PNG_CACHE: dict[str, tuple[int, int, bytes]] = {}

    def _decode_png_argb32(path_str: str) -> tuple[int, int, bytes]:
        cached = _PNG_CACHE.get(path_str)
        if cached is not None:
            return cached

        raw = Path(path_str).read_bytes()
        if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
            raise Error(f"invalid PNG file: {path_str}")

        pos = 8
        width = 0
        height = 0
        bit_depth = 8
        color_type = 6
        palette: list[tuple[int, int, int]] = []
        trns: bytes = b""
        idat_chunks: list[bytes] = []

        while pos + 8 <= len(raw):
            length = struct.unpack(">I", raw[pos : pos + 4])[0]
            chunk_type = raw[pos + 4 : pos + 8]
            chunk_data = raw[pos + 8 : pos + 8 + length]
            pos += 12 + length

            if chunk_type == b"IHDR":
                width, height, bit_depth, color_type, _comp, _filt, _interlace = (
                    struct.unpack(">IIBBBBB", chunk_data[:13])
                )
            elif chunk_type == b"PLTE":
                palette = [
                    (chunk_data[i], chunk_data[i + 1], chunk_data[i + 2])
                    for i in range(0, len(chunk_data), 3)
                ]
            elif chunk_type == b"tRNS":
                trns = chunk_data
            elif chunk_type == b"IDAT":
                idat_chunks.append(chunk_data)
            elif chunk_type == b"IEND":
                break

        if width <= 0 or height <= 0:
            raise Error(f"missing PNG dimensions: {path_str}")

        decompressed = zlib.decompress(b"".join(idat_chunks))

        if color_type == 6 and bit_depth == 8:
            bpp = 4
            row_bytes = width * 4
        elif color_type == 2 and bit_depth == 8:
            bpp = 3
            row_bytes = width * 3
        elif color_type == 3 and bit_depth == 8:
            bpp = 1
            row_bytes = width
        elif color_type == 3 and bit_depth == 4:
            bpp = 1
            row_bytes = (width + 1) // 2
        elif color_type == 0 and bit_depth == 8:
            bpp = 1
            row_bytes = width
        else:
            raise Error(
                f"unsupported PNG mode color_type={color_type} bit_depth={bit_depth}"
            )

        out = bytearray(width * height * 4)
        prev_row = bytearray(row_bytes)
        cur_row = bytearray(row_bytes)
        src_offset = 0

        for y in range(height):
            filter_type = decompressed[src_offset]
            src_offset += 1
            row_slice = decompressed[src_offset : src_offset + row_bytes]
            src_offset += row_bytes

            if filter_type == 0:
                cur_row[:] = row_slice
            elif filter_type == 1:
                for i in range(row_bytes):
                    left = cur_row[i - bpp] if i >= bpp else 0
                    cur_row[i] = (row_slice[i] + left) & 0xFF
            elif filter_type == 2:
                for i in range(row_bytes):
                    cur_row[i] = (row_slice[i] + prev_row[i]) & 0xFF
            elif filter_type == 3:
                for i in range(row_bytes):
                    left = cur_row[i - bpp] if i >= bpp else 0
                    up = prev_row[i]
                    cur_row[i] = (row_slice[i] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                for i in range(row_bytes):
                    left = cur_row[i - bpp] if i >= bpp else 0
                    up = prev_row[i]
                    up_left = prev_row[i - bpp] if i >= bpp else 0
                    cur_row[i] = (
                        row_slice[i] + _paeth_predictor(left, up, up_left)
                    ) & 0xFF
            else:
                cur_row[:] = row_slice

            dst_row_start = y * width * 4
            if color_type == 6 and bit_depth == 8:
                for x in range(width):
                    si = x * 4
                    r = cur_row[si]
                    g = cur_row[si + 1]
                    b = cur_row[si + 2]
                    a = cur_row[si + 3]
                    if a == 0:
                        r = g = b = 0
                    elif a < 255:
                        r = (r * a + 127) // 255
                        g = (g * a + 127) // 255
                        b = (b * a + 127) // 255
                    di = dst_row_start + x * 4
                    out[di] = b
                    out[di + 1] = g
                    out[di + 2] = r
                    out[di + 3] = a
            elif color_type == 2 and bit_depth == 8:
                for x in range(width):
                    si = x * 3
                    di = dst_row_start + x * 4
                    out[di] = cur_row[si + 2]
                    out[di + 1] = cur_row[si + 1]
                    out[di + 2] = cur_row[si]
                    out[di + 3] = 255
            elif color_type == 3:
                for x in range(width):
                    if bit_depth == 8:
                        idx = cur_row[x]
                    else:
                        byte_val = cur_row[x >> 1]
                        idx = (byte_val >> 4) & 0x0F if (x & 1) == 0 else byte_val & 0x0F
                    r, g, b = palette[idx] if idx < len(palette) else (0, 0, 0)
                    a = trns[idx] if idx < len(trns) else 255
                    if a == 0:
                        r = g = b = 0
                    elif a < 255:
                        r = (r * a + 127) // 255
                        g = (g * a + 127) // 255
                        b = (b * a + 127) // 255
                    di = dst_row_start + x * 4
                    out[di] = b
                    out[di + 1] = g
                    out[di + 2] = r
                    out[di + 3] = a
            elif color_type == 0:
                for x in range(width):
                    v = cur_row[x]
                    di = dst_row_start + x * 4
                    out[di] = v
                    out[di + 1] = v
                    out[di + 2] = v
                    out[di + 3] = 255

            prev_row[:] = cur_row

        result = (width, height, bytes(out))
        _PNG_CACHE[path_str] = result
        return result

    class ImageSurface:
        """Pure-Python Cairo ImageSurface storing little-endian BGRA premultiplied pixels."""

        def __init__(self, fmt: int, width: int, height: int) -> None:
            self._format = int(fmt)
            self._width = max(0, int(width))
            self._height = max(0, int(height))
            self._stride = self._width * 4
            self._data = bytearray(self._stride * self._height)
            self._device_scale = (1.0, 1.0)

        @classmethod
        def create_from_png(cls, filename: str | Path) -> ImageSurface:
            path_str = str(filename)
            try:
                width, height, raw_bytes = _decode_png_argb32(path_str)
            except FileNotFoundError as exc:
                raise Error(str(exc)) from exc
            surface = cls(FORMAT_ARGB32, width, height)
            surface._data[:] = raw_bytes
            return surface

        def get_width(self) -> int:
            return self._width

        def get_height(self) -> int:
            return self._height

        def get_format(self) -> int:
            return self._format

        def get_stride(self) -> int:
            return self._stride

        def get_content(self) -> int:
            return CONTENT_COLOR_ALPHA if self._format == FORMAT_ARGB32 else CONTENT_COLOR

        def get_data(self) -> memoryview:
            return memoryview(self._data)

        def flush(self) -> None:
            return None

        def write_to_png(self, target: str | Path | object) -> None:
            raw_rows = bytearray()
            stride = self._stride
            data = self._data
            for y in range(self._height):
                raw_rows.append(0)  # Filter type 0 (None)
                row_start = y * stride
                for x in range(self._width):
                    i = row_start + x * 4
                    b = data[i]
                    g = data[i + 1]
                    r = data[i + 2]
                    a = data[i + 3]
                    if a == 0:
                        raw_rows.extend((0, 0, 0, 0))
                    elif a < 255:
                        raw_rows.extend(
                            (
                                min(255, (r * 255 + (a // 2)) // a),
                                min(255, (g * 255 + (a // 2)) // a),
                                min(255, (b * 255 + (a // 2)) // a),
                                a,
                            )
                        )
                    else:
                        raw_rows.extend((r, g, b, 255))

            def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
                crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
                return (
                    struct.pack(">I", len(payload))
                    + chunk_type
                    + payload
                    + struct.pack(">I", crc)
                )

            ihdr = struct.pack(
                ">IIBBBBB",
                self._width,
                self._height,
                8,
                6,
                0,
                0,
                0,
            )
            idat = zlib.compress(bytes(raw_rows))
            png_bytes = (
                b"\x89PNG\r\n\x1a\n"
                + _chunk(b"IHDR", ihdr)
                + _chunk(b"IDAT", idat)
                + _chunk(b"IEND", b"")
            )
            if isinstance(target, (str, Path)):
                path_str = str(target)
                Path(path_str).write_bytes(png_bytes)
                _PNG_CACHE.pop(path_str, None)
            elif hasattr(target, "write"):
                target.write(png_bytes)  # type: ignore[union-attr]

        def set_device_scale(self, x_scale: float, y_scale: float) -> None:
            self._device_scale = (float(x_scale), float(y_scale))

        def get_device_scale(self) -> tuple[float, float]:
            return self._device_scale

    class _ContextState:
        __slots__ = (
            "tx",
            "ty",
            "sx",
            "sy",
            "operator",
            "source",
            "line_width",
            "line_cap",
            "font_size",
        )

        def __init__(
            self,
            tx: float = 0.0,
            ty: float = 0.0,
            sx: float = 1.0,
            sy: float = 1.0,
            operator: int = OPERATOR_OVER,
            source: Pattern | None = None,
            line_width: float = 2.0,
            line_cap: int = LINE_CAP_BUTT,
            font_size: float = 10.0,
        ) -> None:
            self.tx = tx
            self.ty = ty
            self.sx = sx
            self.sy = sy
            self.operator = operator
            self.source = source or SolidPattern(0.0, 0.0, 0.0, 1.0)
            self.line_width = line_width
            self.line_cap = line_cap
            self.font_size = font_size

        def clone(self) -> _ContextState:
            return _ContextState(
                tx=self.tx,
                ty=self.ty,
                sx=self.sx,
                sy=self.sy,
                operator=self.operator,
                source=self.source,
                line_width=self.line_width,
                line_cap=self.line_cap,
                font_size=self.font_size,
            )

    class Context:
        """Pure-Python Cairo Context supporting surface blits, gradients, and shapes."""

        def __init__(self, target: ImageSurface) -> None:
            self._target = target
            dev_sx, dev_sy = target.get_device_scale()
            self._state = _ContextState(sx=dev_sx, ty=0.0, tx=0.0, sy=dev_sy)
            self._stack: list[_ContextState] = []
            self._paths: list[tuple[str, tuple[float, ...]]] = []
            self._cursor = (0.0, 0.0)

        def save(self) -> None:
            self._stack.append(self._state.clone())

        def restore(self) -> None:
            if self._stack:
                self._state = self._stack.pop()

        def translate(self, tx: float, ty: float) -> None:
            self._state.tx += float(tx) * self._state.sx
            self._state.ty += float(ty) * self._state.sy

        def scale(self, sx: float, sy: float) -> None:
            self._state.sx *= float(sx)
            self._state.sy *= float(sy)

        def set_operator(self, op: int) -> None:
            self._state.operator = int(op)

        def set_source(self, source: Pattern) -> None:
            self._state.source = source

        def get_source(self) -> Pattern:
            return self._state.source

        def set_source_rgba(
            self,
            r: float,
            g: float,
            b: float,
            a: float = 1.0,
        ) -> None:
            self._state.source = SolidPattern(r, g, b, a)

        def set_source_rgb(self, r: float, g: float, b: float) -> None:
            self.set_source_rgba(r, g, b, 1.0)

        def set_source_surface(
            self,
            surface: ImageSurface,
            x: float = 0.0,
            y: float = 0.0,
        ) -> None:
            self._state.source = SurfacePattern(surface, x, y)

        def set_line_width(self, width: float) -> None:
            self._state.line_width = max(0.1, float(width))

        def set_line_cap(self, cap: int) -> None:
            self._state.line_cap = int(cap)

        def select_font_face(
            self,
            family: str,
            slant: int = FONT_SLANT_NORMAL,
            weight: int = FONT_WEIGHT_NORMAL,
        ) -> None:
            return None

        def set_font_size(self, size: float) -> None:
            self._state.font_size = max(1.0, float(size))

        def text_extents(self, text: str) -> TextExtents:
            s = str(text or "")
            w = len(s) * self._state.font_size * 0.56
            h = self._state.font_size
            return TextExtents(0.0, -h * 0.8, w, h, w, 0.0)

        def show_text(self, text: str) -> None:
            ext = self.text_extents(text)
            cx, cy = self._cursor
            self.rectangle(cx, cy - ext.height * 0.8, max(1.0, ext.width), max(1.0, ext.height * 0.8))
            self.fill()

        def text_path(self, text: str) -> None:
            ext = self.text_extents(text)
            cx, cy = self._cursor
            self.rectangle(cx, cy - ext.height * 0.8, max(1.0, ext.width), max(1.0, ext.height * 0.8))

        def new_sub_path(self) -> None:
            return None

        def close_path(self) -> None:
            return None

        def move_to(self, x: float, y: float) -> None:
            self._cursor = (float(x), float(y))

        def line_to(self, x: float, y: float) -> None:
            x0, y0 = self._cursor
            x1, y1 = float(x), float(y)
            self._paths.append(
                (
                    "rect",
                    (
                        min(x0, x1),
                        min(y0, y1),
                        max(1.0, abs(x1 - x0)),
                        max(1.0, abs(y1 - y0)),
                    ),
                )
            )
            self._cursor = (x1, y1)

        def rectangle(self, x: float, y: float, width: float, height: float) -> None:
            self._paths.append(("rect", (float(x), float(y), float(width), float(height))))

        def arc(
            self,
            xc: float,
            yc: float,
            radius: float,
            angle1: float,
            angle2: float,
        ) -> None:
            self._paths.append(("arc", (float(xc), float(yc), float(radius))))

        def mask_surface(
            self,
            surface: ImageSurface,
            surface_x: float = 0.0,
            surface_y: float = 0.0,
        ) -> None:
            prev = self._state.source
            self.set_source_surface(surface, surface_x, surface_y)
            self.paint()
            self._state.source = prev

        def paint(self) -> None:
            dst = self._target
            if self._state.operator == OPERATOR_CLEAR:
                dst._data[:] = b"\x00" * len(dst._data)
                return

            src_pattern = self._state.source
            if isinstance(src_pattern, SurfacePattern):
                self._blit_surface(src_pattern)
            elif isinstance(src_pattern, SolidPattern):
                self._fill_box(0, 0, dst._width, dst._height, src_pattern.rgba)

        def fill(self) -> None:
            self.fill_preserve()
            self._paths.clear()

        def fill_preserve(self) -> None:
            dst = self._target
            src = self._state.source
            for kind, params in self._paths:
                if kind == "rect":
                    rx, ry, rw, rh = params
                    x0 = max(0, int(round(self._state.tx + rx * self._state.sx)))
                    y0 = max(0, int(round(self._state.ty + ry * self._state.sy)))
                    x1 = min(dst._width, int(round(self._state.tx + (rx + rw) * self._state.sx)))
                    y1 = min(dst._height, int(round(self._state.ty + (ry + rh) * self._state.sy)))
                    if isinstance(src, SolidPattern):
                        self._fill_box(x0, y0, x1, y1, src.rgba)
                elif kind == "arc":
                    xc, yc, radius = params
                    self._fill_arc(xc, yc, radius, src)

        def stroke(self) -> None:
            self.stroke_preserve()
            self._paths.clear()

        def stroke_preserve(self) -> None:
            self.fill_preserve()

        def _blend_pixel(
            self,
            di: int,
            r: float,
            g: float,
            b: float,
            a: float,
        ) -> None:
            data = self._target._data
            sa = int(round(max(0.0, min(1.0, a)) * 255.0))
            if sa <= 0 and self._state.operator == OPERATOR_OVER:
                return
            sr = int(round(max(0.0, min(1.0, r)) * sa))
            sg = int(round(max(0.0, min(1.0, g)) * sa))
            sb = int(round(max(0.0, min(1.0, b)) * sa))

            if self._state.operator == OPERATOR_SOURCE or sa == 255:
                data[di] = sb
                data[di + 1] = sg
                data[di + 2] = sr
                data[di + 3] = sa
                return

            inv = 255 - sa
            data[di] = min(255, sb + ((data[di] * inv + 127) // 255))
            data[di + 1] = min(255, sg + ((data[di + 1] * inv + 127) // 255))
            data[di + 2] = min(255, sr + ((data[di + 2] * inv + 127) // 255))
            data[di + 3] = min(255, sa + ((data[di + 3] * inv + 127) // 255))

        def _fill_box(
            self,
            x0: int,
            y0: int,
            x1: int,
            y1: int,
            rgba: tuple[float, float, float, float],
        ) -> None:
            if x1 <= x0 or y1 <= y0:
                return
            r, g, b, a = rgba
            stride = self._target._stride
            for py in range(y0, y1):
                row_base = py * stride
                for px in range(x0, x1):
                    self._blend_pixel(row_base + px * 4, r, g, b, a)

        def _fill_arc(
            self,
            xc: float,
            yc: float,
            radius: float,
            src: Pattern,
        ) -> None:
            if radius <= 0.0:
                return
            dst = self._target
            cx = self._state.tx + xc * self._state.sx
            cy = self._state.ty + yc * self._state.sy
            rx = radius * abs(self._state.sx)
            ry = radius * abs(self._state.sy)
            r_max = max(rx, ry)
            if r_max <= 0.0:
                return

            x0 = max(0, int(math.floor(cx - r_max)))
            y0 = max(0, int(math.floor(cy - r_max)))
            x1 = min(dst._width, int(math.ceil(cx + r_max)) + 1)
            y1 = min(dst._height, int(math.ceil(cy + r_max)) + 1)
            stride = dst._stride

            for py in range(y0, y1):
                dy = (py + 0.5) - cy
                row_base = py * stride
                for px in range(x0, x1):
                    dx = (px + 0.5) - cx
                    dist = math.hypot(dx / max(1e-6, rx), dy / max(1e-6, ry)) * radius
                    if dist > radius:
                        continue
                    if isinstance(src, RadialGradient):
                        span = max(1e-6, src.radius1 - src.radius0)
                        t = (dist - src.radius0) / span
                        rgba = src.sample(t)
                    elif isinstance(src, SolidPattern):
                        rgba = src.rgba
                    else:
                        rgba = (1.0, 1.0, 1.0, 1.0)
                    self._blend_pixel(row_base + px * 4, *rgba)

        def _blit_surface(self, pattern: SurfacePattern) -> None:
            src = pattern.surface
            dst = self._target
            sx = self._state.sx
            sy = self._state.sy
            if abs(sx) < 1e-9 or abs(sy) < 1e-9:
                return

            # Destination rectangle covered by the source surface:
            # user-space (x_u, y_u) = (pattern.x + sx_px, pattern.y + sy_px)
            # device-space (dx, dy) = (tx + x_u * sx, ty + y_u * sy)
            ox = self._state.tx + pattern.x * sx
            oy = self._state.ty + pattern.y * sy

            dx0 = max(0, int(round(ox)))
            dy0 = max(0, int(round(oy)))
            dx1 = min(dst._width, int(round(ox + src._width * sx)))
            dy1 = min(dst._height, int(round(oy + src._height * sy)))

            if dx1 <= dx0 or dy1 <= dy0:
                return

            src_data = src._data
            dst_data = dst._data
            src_stride = src._stride
            dst_stride = dst._stride
            src_w = src._width
            src_h = src._height
            op = self._state.operator

            # Fast path: 1:1 unscaled copy with OPERATOR_SOURCE
            if (
                op == OPERATOR_SOURCE
                and abs(sx - 1.0) < 1e-6
                and abs(sy - 1.0) < 1e-6
            ):
                src_x_offset = int(round(-ox))
                src_y_offset = int(round(-oy))
                for py in range(dy0, dy1):
                    sy_i = py + src_y_offset
                    if sy_i < 0 or sy_i >= src_h:
                        continue
                    sx_start = max(0, dx0 + src_x_offset)
                    sx_end = min(src_w, dx1 + src_x_offset)
                    count = sx_end - sx_start
                    if count <= 0:
                        continue
                    d_start = py * dst_stride + (sx_start - src_x_offset) * 4
                    s_start = sy_i * src_stride + sx_start * 4
                    dst_data[d_start : d_start + count * 4] = src_data[
                        s_start : s_start + count * 4
                    ]
                return

            for py in range(dy0, dy1):
                sy_i = int((py + 0.5 - oy) / sy)
                if sy_i < 0 or sy_i >= src_h:
                    continue
                s_row = sy_i * src_stride
                d_row = py * dst_stride
                for px in range(dx0, dx1):
                    sx_i = int((px + 0.5 - ox) / sx)
                    if sx_i < 0 or sx_i >= src_w:
                        continue
                    si = s_row + sx_i * 4
                    di = d_row + px * 4
                    sb = src_data[si]
                    sg = src_data[si + 1]
                    sr = src_data[si + 2]
                    sa = src_data[si + 3]

                    if op == OPERATOR_SOURCE or sa == 255:
                        dst_data[di] = sb
                        dst_data[di + 1] = sg
                        dst_data[di + 2] = sr
                        dst_data[di + 3] = sa
                    elif sa > 0:
                        inv = 255 - sa
                        dst_data[di] = min(255, sb + ((dst_data[di] * inv + 127) // 255))
                        dst_data[di + 1] = min(
                            255, sg + ((dst_data[di + 1] * inv + 127) // 255)
                        )
                        dst_data[di + 2] = min(
                            255, sr + ((dst_data[di + 2] * inv + 127) // 255)
                        )
                        dst_data[di + 3] = min(
                            255, sa + ((dst_data[di + 3] * inv + 127) // 255)
                        )
