"""
zpl_parser.py

Interpretador ZPL simplificado, porém funcional, que renderiza os comandos
mais comuns de etiquetas ZPL (texto, código de barras Code128, QR Code,
caixas/linhas) em uma imagem PIL, sem depender de nenhum serviço externo.

Não é um emulador 100% completo da linguagem ZPL (que é enorme), mas cobre
o que é usado na grande maioria das etiquetas geradas por ERPs, e-commerces
e transportadoras: ^FO/^FT, ^A, ^FD/^FS, ^BC, ^BQ, ^GB, ^BY, ^CF, ^PW, ^LL,
^LH, ^FB e imagens (^GF, ~DG + ^XG), inclusive comprimidas (:Z64:/:B64:).

Comandos não suportados são simplesmente ignorados (não quebram a
renderização).
"""

import base64
import io
import os
import re
import sys
import zlib
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    import barcode
    from barcode.writer import ImageWriter
except ImportError:
    barcode = None

try:
    import qrcode
except ImportError:
    qrcode = None


def _base_dir():
    # Funciona tanto rodando como script quanto empacotado com PyInstaller
    if getattr(sys, "_MEIPASS", None):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


ASSETS_DIR = os.path.join(_base_dir(), "assets", "fonts")
FONT_REGULAR = os.path.join(ASSETS_DIR, "DejaVuSansMono.ttf")
FONT_BOLD = os.path.join(ASSETS_DIR, "DejaVuSansMono-Bold.ttf")


def split_labels(zpl_text: str):
    """Divide um arquivo ZPL que pode conter várias etiquetas (vários blocos
    ^XA ... ^XZ) em uma lista de strings, uma por etiqueta."""
    labels = []
    buf = []
    inside = False
    i = 0
    text = zpl_text
    while i < len(text):
        if text[i:i + 3].upper() == "^XA":
            inside = True
            buf = [text[i:i + 3]]
            i += 3
            continue
        if text[i:i + 3].upper() == "^XZ":
            if inside:
                buf.append(text[i:i + 3])
                labels.append("".join(buf))
                inside = False
                buf = []
            i += 3
            continue
        if inside:
            buf.append(text[i])
        i += 1
    # fallback: comandos ZPL soltos, sem ^XA/^XZ
    if not labels and "^" in text:
        labels.append(text)
    return labels


# ---------------------------------------------------------------------- #
# Imagens ZPL (~DG / ^XG / ^GF)
# ---------------------------------------------------------------------- #
_DG_RE = re.compile(r"~DG([^,\^~]*),(\d+),(\d+),", re.IGNORECASE)
_XZ_RE = re.compile(r"\^XZ", re.IGNORECASE)
_HEX_CHARS = set("0123456789ABCDEFabcdef")
_RLE_CHARS = set("GHIJKLMNOPQRSTUVWXYghijklmnopqrstuvwxyz,!:")
_WS_CHARS = set(" \t\r\n")
# comandos que desenham alguma coisa (usado para descartar blocos vazios,
# como o "^XA^IDR:ARQ.GRF^FS^XZ" que só apaga uma imagem da memória)
_DRAWING_RE = re.compile(r"\^(?:FD|GB|GC|GD|GE|XG|GF|IM)", re.IGNORECASE)


def normalize_graphic_name(name: str) -> str:
    """'R:DEMO.GRF' -> 'DEMO.GRF' (ignora a letra da unidade, maiúsculas)."""
    name = (name or "").strip()
    if ":" in name:
        name = name.split(":", 1)[1]
    name = name.upper()
    if name and "." not in name:
        name += ".GRF"
    return name


def _decode_hex_rle(text: str, bytes_per_row: int) -> bytes:
    """Decodifica o formato ASCII hexadecimal do ZPL, com a compressão RLE
    (G-Y = repete 1..19, g-z = repete 20..400, ',' completa a linha com 0,
    '!' completa com F, ':' repete a linha anterior)."""
    row_len = bytes_per_row * 2
    rows = []
    row = ""
    prev = ""
    count = 0

    def push(chunk):
        nonlocal row, prev
        row += chunk
        while len(row) >= row_len:
            prev = row[:row_len]
            rows.append(prev)
            row = row[row_len:]

    for ch in text:
        if ch in _WS_CHARS:
            continue
        if ch in _HEX_CHARS:
            push(ch * (count or 1))
            count = 0
        elif "G" <= ch <= "Y":
            count += ord(ch) - ord("F")
        elif "g" <= ch <= "z":
            count += 20 * (ord(ch) - ord("f"))
        elif ch == ",":
            push("0" * (row_len - len(row)) if row else "0" * row_len)
            count = 0
        elif ch == "!":
            push("F" * (row_len - len(row)) if row else "F" * row_len)
            count = 0
        elif ch == ":":
            if not row and prev:
                push(prev)
            count = 0
    if len(row) >= 2:
        rows.append(row.ljust(row_len, "0"))
    try:
        return bytes.fromhex("".join(rows))
    except ValueError:
        return b""


def decode_graphic(payload: str, bytes_per_row: int, total: int = 0):
    """Converte os dados de uma imagem ZPL (hex, hex+RLE, :B64: ou :Z64:) em
    uma imagem PIL no modo '1' (0 = preto, 1 = branco). Retorna None se os
    dados forem inválidos."""
    if bytes_per_row <= 0:
        return None
    payload = payload.strip()
    try:
        if payload.startswith(":"):
            parts = payload.split(":")           # ['', 'Z64', dados, crc]
            kind = parts[1].upper() if len(parts) > 1 else ""
            raw = base64.b64decode("".join(parts[2].split()) if len(parts) > 2 else "")
            if kind == "Z64":
                raw = zlib.decompress(raw)
            elif kind != "B64":
                return None
            # o conteúdo pode ser o bitmap binário ou o texto hex (+RLE)
            if total and len(raw) == total:
                data = raw
            elif raw and all(chr(b) in _HEX_CHARS | _RLE_CHARS | _WS_CHARS for b in raw):
                data = _decode_hex_rle(raw.decode("ascii"), bytes_per_row)
            else:
                data = raw
        else:
            data = _decode_hex_rle(payload, bytes_per_row)
    except Exception:
        return None

    rows = len(data) // bytes_per_row
    if rows <= 0:
        return None
    data = data[: rows * bytes_per_row]
    # em ZPL, bit 1 = ponto preto; no modo '1' do PIL, 0 = preto
    inverted = data.translate(bytes(255 - i for i in range(256)))
    return Image.frombytes("1", (bytes_per_row * 8, rows), inverted)


class ParsedLabel:
    """Uma etiqueta (texto ^XA...^XZ) e as imagens ~DG disponíveis nela."""

    def __init__(self, zpl, graphics):
        self.zpl = zpl
        self.graphics = graphics


def _read_dg(text, match, graphics):
    """Lê os dados do ~DG que começa em `match`, registra em `graphics` e
    devolve a posição logo após os dados."""
    start = match.end()
    end = start
    n = len(text)
    while end < n and text[end] not in "^~":
        end += 1
    img = decode_graphic(text[start:end], int(match.group(3)), int(match.group(2)))
    name = normalize_graphic_name(match.group(1))
    if img is not None:
        graphics[name] = img
    else:
        graphics.pop(name, None)
    return end


def has_drawing(zpl_label: str) -> bool:
    """True se o bloco desenha algo (texto, caixa, código, imagem...)."""
    return bool(_DRAWING_RE.search(zpl_label))


def parse_labels(zpl_text: str):
    """Como split_labels, mas processa o arquivo em ordem e registra as
    imagens ~DG (que podem vir ANTES do ^XA e ser redefinidas com o mesmo
    nome a cada etiqueta). Retorna uma lista de ParsedLabel."""
    graphics = {}
    labels = []
    text = zpl_text
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch == "~" and text[i + 1:i + 3].upper() == "DG":
            m = _DG_RE.match(text, i)
            if m:
                i = _read_dg(text, m, graphics)
                continue
        elif ch == "^" and text[i + 1:i + 3].upper() == "XA":
            xz = _XZ_RE.search(text, i + 3)
            end = xz.end() if xz else n
            block = text[i:end]
            for m in _DG_RE.finditer(block):      # ~DG dentro do próprio bloco
                _read_dg(block, m, graphics)
            labels.append(ParsedLabel(block, dict(graphics)))
            i = end
            continue
        i += 1
    if not labels and "^" in text:
        labels.append(ParsedLabel(text, dict(graphics)))
    return labels


class ZPLRenderer:
    """Renderiza uma etiqueta ZPL (uma única string ^XA...^XZ) em uma
    imagem PIL (modo RGB, fundo branco)."""

    KNOWN_2 = {
        "XA", "XZ", "FO", "FT", "FD", "FS", "BY", "BC", "BQ", "GB", "GC",
        "GD", "CF", "FB", "LL", "PW", "LH", "MU", "CI", "PQ", "FX", "SD",
        "MD", "FR", "FH", "PR", "GF",
    }

    def __init__(self, dpi=203, width_mm=100, height_mm=150):
        self.dpi = dpi
        self.dots_per_mm = dpi / 25.4
        self.default_w = int(round(width_mm * self.dots_per_mm))
        self.default_h = int(round(height_mm * self.dots_per_mm))
        self._pending_barcode = None

    # ------------------------------------------------------------------ #
    # Tokenização
    # ------------------------------------------------------------------ #
    def _tokenize(self, zpl):
        tokens = []
        i = 0
        n = len(zpl)
        while i < n:
            ch = zpl[i]
            if ch in "^~":
                two = zpl[i + 1:i + 3].upper()
                if two in self.KNOWN_2:
                    code = two
                    start_params = i + 3
                elif zpl[i + 1:i + 2].upper() == "A":
                    code = "A"
                    start_params = i + 2
                else:
                    code = two
                    start_params = i + 3

                if code == "FD":
                    # dados do campo: vão até ^FS (ou fim da string)
                    end = zpl.find("^FS", start_params)
                    if end == -1:
                        end = n
                    tokens.append((code, zpl[start_params:end]))
                    i = end
                    continue
                else:
                    j = start_params
                    while j < n and zpl[j] not in "^~":
                        j += 1
                    tokens.append((code, zpl[start_params:j]))
                    i = j
                    continue
            i += 1
        return tokens

    # ------------------------------------------------------------------ #
    # Render principal
    # ------------------------------------------------------------------ #
    def render(self, zpl_label: str, graphics=None) -> Image.Image:
        """`graphics` = imagens ~DG disponíveis (nome normalizado -> imagem)."""
        graphics = graphics or {}
        tokens = self._tokenize(zpl_label)
        self._pending_barcode = None

        canvas_w = self.default_w
        canvas_h = self.default_h
        home_x, home_y = 0, 0
        width_set = height_set = False
        need_w = need_h = 0
        bitmaps = {}   # índice do token -> (imagem, ampliação x, ampliação y)

        # primeiro passe: tamanho real (^PW / ^LL / ^LH) e imagens (^XG / ^GF)
        fx, fy = 0, 0
        for idx, (code, params) in enumerate(tokens):
            if code == "PW":
                canvas_w = self._first_int(params, canvas_w)
                width_set = True
            elif code == "LL":
                canvas_h = self._first_int(params, canvas_h)
                height_set = True
            elif code == "LH":
                parts = params.split(",")
                if len(parts) >= 2:
                    home_x = self._to_int(parts[0], 0)
                    home_y = self._to_int(parts[1], 0)
            elif code in ("FO", "FT"):
                parts = params.split(",")
                fx = home_x + (self._to_int(parts[0], 0) if len(parts) > 0 else 0)
                fy = home_y + (self._to_int(parts[1], 0) if len(parts) > 1 else 0)
            elif code in ("XG", "GF"):
                found = self._resolve_bitmap(code, params, graphics)
                if found is not None:
                    bitmaps[idx] = found
                    bmp, mx, my = found
                    need_w = max(need_w, fx + bmp.width * mx)
                    need_h = max(need_h, fy + bmp.height * my)

        # sem ^PW/^LL explícitos, a etiqueta cresce para caber a imagem
        if not width_set:
            canvas_w = max(canvas_w, need_w)
        if not height_set:
            canvas_h = max(canvas_h, need_h)

        img = Image.new("RGB", (max(canvas_w, 10), max(canvas_h, 10)), "white")
        draw = ImageDraw.Draw(img)

        cur_x, cur_y = home_x, home_y
        font_height = 25
        font_bold = False
        bc_module_width = 2
        bc_height = 100
        fb_width = None

        for idx, (code, params) in enumerate(tokens):
            if code in ("XG", "GF"):
                if idx in bitmaps:
                    self._draw_bitmap(img, cur_x, cur_y, *bitmaps[idx])

            elif code in ("FO", "FT"):
                parts = params.split(",")
                cur_x = home_x + self._to_int(parts[0], 0) if len(parts) > 0 else home_x
                cur_y = home_y + self._to_int(parts[1], 0) if len(parts) > 1 else home_y

            elif code == "A":
                pieces = params.split(",")
                font_orient = pieces[0] if pieces else "0N"
                font_bold = font_orient[:1] in ("0", "B", "D", "E")
                if len(pieces) > 1:
                    font_height = self._to_int(pieces[1], font_height)

            elif code == "CF":
                pieces = params.split(",")
                if len(pieces) > 1:
                    font_height = self._to_int(pieces[1], font_height)

            elif code == "BY":
                pieces = params.split(",")
                if len(pieces) > 0 and pieces[0]:
                    bc_module_width = self._to_int(pieces[0], bc_module_width)
                if len(pieces) > 2 and pieces[2]:
                    bc_height = self._to_int(pieces[2], bc_height)

            elif code == "FB":
                pieces = params.split(",")
                if pieces and pieces[0]:
                    fb_width = self._to_int(pieces[0], None)

            elif code == "GB":
                self._draw_box(draw, cur_x, cur_y, params)

            elif code == "BC":
                pieces = params.split(",")
                h = bc_height
                if len(pieces) > 1 and pieces[1]:
                    h = self._to_int(pieces[1], bc_height)
                self._pending_barcode = ("code128", bc_module_width, h)

            elif code == "BQ":
                self._pending_barcode = ("qr", None, None)

            elif code == "FD":
                data = params
                pending = self._pending_barcode
                if pending and pending[0] == "code128":
                    self._draw_code128(img, draw, cur_x, cur_y, data, pending[1], pending[2])
                elif pending and pending[0] == "qr":
                    self._draw_qr(img, cur_x, cur_y, data)
                else:
                    self._draw_text(draw, cur_x, cur_y, data, font_height,
                                    font_bold, fb_width)
                self._pending_barcode = None
                fb_width = None

        return img

    # ------------------------------------------------------------------ #
    # Helpers de desenho
    # ------------------------------------------------------------------ #
    def _get_font(self, height_px, bold=False):
        height_px = max(6, int(height_px))
        path = FONT_BOLD if bold else FONT_REGULAR
        try:
            return ImageFont.truetype(path, height_px)
        except Exception:
            return ImageFont.load_default()

    def _draw_text(self, draw, x, y, text, height, bold, fb_width):
        if not text:
            return
        font = self._get_font(height, bold)
        if fb_width:
            lines = self._wrap_text(text, font, fb_width)
            line_h = height + 4
            for k, line in enumerate(lines):
                draw.text((x, y + k * line_h), line, font=font, fill="black")
        else:
            draw.text((x, y), text, font=font, fill="black")

    def _wrap_text(self, text, font, max_width):
        words = text.split(" ")
        lines = []
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip()
            bbox = font.getbbox(trial)
            if bbox[2] - bbox[0] <= max_width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def _resolve_bitmap(self, code, params, graphics):
        """Devolve (imagem modo '1', ampliação x, ampliação y) ou None."""
        if code == "XG":                       # ^XGnome,mx,my
            parts = params.split(",")
            bmp = graphics.get(normalize_graphic_name(parts[0]))
            if bmp is None:
                return None
            mx = max(self._to_int(parts[1], 1), 1) if len(parts) > 1 else 1
            my = max(self._to_int(parts[2], 1), 1) if len(parts) > 2 else 1
            return bmp, mx, my
        # ^GFformato,total,total2,bytes_por_linha,dados
        parts = params.split(",", 4)
        if len(parts) < 5 or parts[0].strip().upper() != "A":
            return None
        bpr = self._to_int(parts[3], 0)
        total = self._to_int(parts[1], 0)
        bmp = decode_graphic(parts[4], bpr, total)
        return (bmp, 1, 1) if bmp is not None else None

    @staticmethod
    def _draw_bitmap(img, x, y, bmp, mx, my):
        if mx != 1 or my != 1:
            bmp = bmp.resize((bmp.width * mx, bmp.height * my), Image.NEAREST)
        # só os pontos pretos são impressos; o branco não apaga o que já existe
        mask = ImageOps.invert(bmp.convert("L"))
        img.paste((0, 0, 0), (x, y), mask)

    def _draw_box(self, draw, x, y, params):
        pieces = params.split(",")
        w = self._to_int(pieces[0], 1) if len(pieces) > 0 else 1
        h = self._to_int(pieces[1], 1) if len(pieces) > 1 else 1
        thickness = self._to_int(pieces[2], 1) if len(pieces) > 2 else 1
        color = "black"
        if len(pieces) > 3 and pieces[3].strip().upper() == "W":
            color = "white"
        if w <= thickness * 2 or h <= thickness * 2:
            draw.rectangle([x, y, x + max(w, 1), y + max(h, 1)], fill=color)
        else:
            draw.rectangle([x, y, x + w, y + h], outline=color, width=max(thickness, 1))

    def _draw_code128(self, img, draw, x, y, data, module_width, height):
        if barcode is None or not data:
            draw.text((x, y), f"[BARCODE:{data}]", fill="black")
            return
        try:
            CODE128 = barcode.get_barcode_class("code128")
            writer = ImageWriter()
            writer.dpi = self.dpi
            bc = CODE128(data, writer=writer)
            buf = io.BytesIO()
            bc.write(buf, options={
                "module_height": max(height, 5) / self.dots_per_mm,
                "module_width": max(module_width, 1) * 25.4 / self.dpi,
                "quiet_zone": 1.0,
                "write_text": False,
            })
            buf.seek(0)
            bc_img = Image.open(buf).convert("RGB")
            img.paste(bc_img, (x, y))
        except Exception:
            draw.text((x, y), f"[BARCODE:{data}]", fill="black")

    def _draw_qr(self, img, x, y, data):
        if qrcode is None or not data:
            return
        content = data
        if content[:2] in ("A,", "M,", "H,", "L,", "Q,"):
            content = content[2:]
        try:
            qr = qrcode.QRCode(border=1, box_size=4)
            qr.add_data(content)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            img.paste(qr_img, (x, y))
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_int(s, default):
        try:
            return int(float(s.strip()))
        except Exception:
            return default

    @staticmethod
    def _first_int(params, default):
        parts = params.split(",")
        return ZPLRenderer._to_int(parts[0], default) if parts else default
