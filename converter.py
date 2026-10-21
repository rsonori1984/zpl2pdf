"""
converter.py

Orquestra a conversão de arquivos .zpl / .txt / .zip contendo etiquetas ZPL
para um único PDF pronto para impressão, no formato de etiqueta 10x15 cm.

Fluxo:
  1. Recebe uma lista de caminhos de arquivo (.zpl, .txt ou .zip)
  2. Se for .zip, extrai e procura arquivos .zpl/.txt dentro
  3. Lê o conteúdo de cada arquivo e detecta se é ZPL válido
  4. Divide o conteúdo em etiquetas individuais (^XA...^XZ)
  5. Renderiza cada etiqueta como imagem via ZPLRenderer
  6. Ajusta cada imagem para a página 10x15 cm
  7. Monta um PDF (uma página por etiqueta) com reportlab
"""

import os
import shutil
import tempfile
import zipfile

from PIL import Image
from reportlab.lib.pagesizes import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas

from zpl_parser import ZPLRenderer, split_labels

LABEL_WIDTH_MM = 100.0
LABEL_HEIGHT_MM = 150.0

SUPPORTED_EXTENSIONS = (".zpl", ".txt")


class ConversionResult:
    def __init__(self):
        self.pdf_path = None
        self.total_labels = 0
        self.files_processed = []
        self.warnings = []


def is_probably_zpl(text: str) -> bool:
    """Heurística simples para saber se um texto contém comandos ZPL."""
    if not text:
        return False
    upper = text.upper()
    return "^XA" in upper or "^FO" in upper or "^FD" in upper


def _safe_extract(zf: zipfile.ZipFile, dest: str):
    """Extrai o zip evitando 'zip slip' (caminhos fora da pasta destino)."""
    dest_real = os.path.realpath(dest)
    for member in zf.infolist():
        target = os.path.realpath(os.path.join(dest, member.filename))
        if not (target == dest_real or target.startswith(dest_real + os.sep)):
            continue
        zf.extract(member, dest)


def collect_input_files(paths):
    """Retorna (lista de .zpl/.txt reais, pasta temporária ou None)."""
    files = []
    tmp_dir = None

    for p in paths:
        ext = os.path.splitext(p)[1].lower()
        if ext == ".zip":
            if tmp_dir is None:
                tmp_dir = tempfile.mkdtemp(prefix="zpl2pdf_")
            with zipfile.ZipFile(p, "r") as zf:
                extract_dir = os.path.join(tmp_dir, os.path.splitext(os.path.basename(p))[0])
                os.makedirs(extract_dir, exist_ok=True)
                _safe_extract(zf, extract_dir)
                for root, _dirs, fnames in os.walk(extract_dir):
                    for fn in sorted(fnames):
                        if os.path.splitext(fn)[1].lower() in SUPPORTED_EXTENSIONS:
                            files.append(os.path.join(root, fn))
        elif ext in SUPPORTED_EXTENSIONS:
            files.append(p)

    return files, tmp_dir


def read_text_any_encoding(path):
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


def convert_files_to_pdf(input_paths, output_pdf_path, dpi=203, progress_callback=None):
    """Converte os arquivos de entrada em um único PDF de etiquetas 10x15cm.

    progress_callback(current, total, message) reporta o progresso.
    """
    result = ConversionResult()
    files, tmp_dir = collect_input_files(input_paths)

    try:
        if not files:
            result.warnings.append(
                "Nenhum arquivo .zpl ou .txt encontrado nas entradas fornecidas."
            )
            return result

        renderer = ZPLRenderer(dpi=dpi, width_mm=LABEL_WIDTH_MM, height_mm=LABEL_HEIGHT_MM)
        all_label_images = []

        for idx, fpath in enumerate(files):
            if progress_callback:
                progress_callback(idx, len(files), f"Lendo {os.path.basename(fpath)}")

            text = read_text_any_encoding(fpath)

            if not is_probably_zpl(text):
                result.warnings.append(
                    f"'{os.path.basename(fpath)}' não parece conter comandos ZPL válidos e foi ignorado."
                )
                continue

            labels = split_labels(text)
            if not labels:
                result.warnings.append(
                    f"'{os.path.basename(fpath)}' não contém blocos ^XA...^XZ reconhecíveis."
                )
                continue

            for lbl in labels:
                try:
                    all_label_images.append(renderer.render(lbl))
                except Exception as e:
                    result.warnings.append(
                        f"Falha ao renderizar uma etiqueta de '{os.path.basename(fpath)}': {e}"
                    )

            result.files_processed.append(os.path.basename(fpath))

        if not all_label_images:
            result.warnings.append("Nenhuma etiqueta pôde ser renderizada.")
            return result

        # Uma página de 100x150mm por etiqueta
        page_w = LABEL_WIDTH_MM * mm
        page_h = LABEL_HEIGHT_MM * mm
        c = pdfcanvas.Canvas(output_pdf_path, pagesize=(page_w, page_h))

        for i, img in enumerate(all_label_images):
            if progress_callback:
                progress_callback(i, len(all_label_images),
                                  f"Gerando página {i + 1}/{len(all_label_images)}")

            # Mantém a proporção e alinha ao topo-esquerda da página, como a
            # impressora térmica faria (sem esticar nem centralizar).
            img_w, img_h = img.size
            scale = min(page_w / img_w, page_h / img_h)
            draw_w = img_w * scale
            draw_h = img_h * scale
            off_x = 0
            off_y = page_h - draw_h

            c.drawImage(ImageReader(img), off_x, off_y, width=draw_w, height=draw_h)
            c.showPage()

        c.save()

        result.pdf_path = output_pdf_path
        result.total_labels = len(all_label_images)
        return result

    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
