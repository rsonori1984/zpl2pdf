"""
zpl_parser.py

Interpretador ZPL simplificado, porém funcional, que renderiza os comandos
mais comuns de etiquetas ZPL (texto, código de barras Code128, QR Code,
caixas/linhas) em uma imagem PIL, sem depender de nenhum serviço externo.

Não é um emulador 100% completo da linguagem ZPL (que é enorme), mas cobre
o que é usado na grande maioria das etiquetas geradas por ERPs, e-commerces
e transportadoras: ^FO/^FT, ^A, ^FD/^FS, ^BC, ^BQ, ^GB, ^BY, ^CF, ^PW, ^LL,
^LH, ^FB.

Comandos não suportados são simplesmente ignorados (não quebram a
renderização).
"""

import io
import os
import sys
from PIL import Image, ImageDraw, ImageFont

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
    def render(self, zpl_label: str) -> Image.Image:
        tokens = self._tokenize(zpl_label)
        self._pending_barcode = None

        canvas_w = self.default_w
        canvas_h = self.default_h
        home_x, home_y = 0, 0

        # primeiro passe: tamanho real (^PW / ^LL / ^LH)
        for code, params in tokens:
            if code == "PW":
                canvas_w = self._first_int(params, canvas_w)
            elif code == "LL":
                canvas_h = self._first_int(params, canvas_h)
            elif code == "LH":
                parts = params.split(",")
                if len(parts) >= 2:
                    home_x = self._to_int(parts[0], 0)
                    home_y = self._to_int(parts[1], 0)

        img = Image.new("RGB", (max(canvas_w, 10), max(canvas_h, 10)), "white")
        draw = ImageDraw.Draw(img)

        cur_x, cur_y = home_x, home_y
        font_height = 25
        font_bold = False
        bc_module_width = 2
        bc_height = 100
        fb_width = None

        for code, params in tokens:
            if code in ("FO", "FT"):
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
