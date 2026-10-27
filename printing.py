"""
printing.py

Impressão das etiquetas usando o sistema de impressão nativo do Qt
(QtPrintSupport), que funciona em Windows (drivers do Windows), macOS e
Linux (CUPS) e permite escolher entre várias impressoras.

Como cada etiqueta é impressa:

* Tamanho físico: a etiqueta sai com 100x150 mm. Se o papel escolhido já é
  de etiqueta (10x15 cm, 4x6 polegadas...), ela preenche a página; em outro
  papel (por exemplo A4), sai no canto superior esquerdo, no tamanho real.
  O papel que o usuário escolhe no diálogo é respeitado.
* Nitidez: a imagem é redimensionada aqui, uma vez, para a resolução da
  impressora e entregue em preto e branco puro, 1 ponto da imagem para cada
  ponto da impressora. Assim o driver não precisa reamostrar (o que borra
  as barras dos códigos de barras).
"""

import math

from PIL import Image
from PySide6.QtCore import QRectF, QSizeF
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter
from PySide6.QtPrintSupport import QPrinter

LABEL_WIDTH_MM = 100.0
LABEL_HEIGHT_MM = 150.0

# Acima disso a imagem é gerada em resolução menor e ampliada por um fator
# inteiro (impressoras de 1200 dpi não precisam de uma imagem gigante).
MAX_RENDER_DPI = 600

# Ponto vira preto se a luminosidade for menor que este valor (0-255). Um
# pouco acima de 128 deixa traços finos levemente mais firmes ao reduzir.
BLACK_THRESHOLD = 160


def apply_label_page(printer: QPrinter):
    """Papel inicial sugerido: 100x150 mm, retrato, sem margens. O usuário
    pode trocar no diálogo; essa escolha é respeitada."""
    printer.setPageSize(QPageSize(QSizeF(LABEL_WIDTH_MM, LABEL_HEIGHT_MM),
                                  QPageSize.Unit.Millimeter))
    printer.setPageOrientation(QPageLayout.Orientation.Portrait)
    printer.setFullPage(True)


def make_printer() -> QPrinter:
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    apply_label_page(printer)
    return printer


def is_label_page(width_mm: float, height_mm: float) -> bool:
    """True para papéis de etiqueta (proporção 2:3, de uns 8 a 13 cm de largura)."""
    if width_mm <= 0 or height_mm <= 0:
        return False
    return 80 <= width_mm <= 130 and abs(height_mm / width_mm - 1.5) <= 0.1


def compute_target(img_w, img_h, page_w_px, page_h_px, dpi):
    """Tamanho (em pontos da impressora) em que a etiqueta deve ser impressa.

    Papel de etiqueta: ocupa a página toda. Outro papel: tamanho físico de
    100x150 mm, limitado ao tamanho da página. Em ambos, mantém a proporção.
    """
    px_per_mm = dpi / 25.4
    if is_label_page(page_w_px / px_per_mm, page_h_px / px_per_mm):
        box_w, box_h = page_w_px, page_h_px
    else:
        box_w = min(page_w_px, LABEL_WIDTH_MM * px_per_mm)
        box_h = min(page_h_px, LABEL_HEIGHT_MM * px_per_mm)
    scale = min(box_w / img_w, box_h / img_h)
    return img_w * scale, img_h * scale


def pil_to_bilevel_qimage(img, width, height, mono=False) -> QImage:
    """Redimensiona para width x height e devolve um QImage só com preto e
    branco (sem tons de cinza).

    mono=True gera 1 bit por ponto (lossless em PDF); mono=False gera 8 bits
    (valores 0 ou 255), formato que qualquer driver de impressora aceita.
    """
    gray = img.convert("L")
    if gray.size != (width, height):
        gray = gray.resize((width, height), Image.LANCZOS)
    bw = gray.point(lambda p: 255 if p >= BLACK_THRESHOLD else 0)
    if not mono:
        qimg = QImage(bw.tobytes(), width, height, width, QImage.Format.Format_Grayscale8)
        return qimg.copy()  # desvincula do buffer de bytes temporário
    bits = bw.convert("1")
    qimg = QImage(bits.tobytes(), width, height, (width + 7) // 8, QImage.Format.Format_Mono)
    # PIL: bit 1 = branco. Qt: o bit é o índice da tabela de cores.
    qimg.setColorTable([0xFF000000, 0xFFFFFFFF])
    return qimg.copy()


def print_images(images, printer: QPrinter, progress=None):
    """Imprime as imagens (PIL) na impressora, uma etiqueta por página.

    progress(feitas, total), se informado, é chamado após cada etiqueta.
    """
    if not images:
        raise ValueError("Não há etiquetas para imprimir.")

    painter = QPainter()
    if not painter.begin(printer):
        raise RuntimeError(
            "Não foi possível iniciar a impressão. Verifique se a impressora "
            "está ligada e disponível."
        )
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        dpi = printer.resolution() or 203
        # 1 bit só quando a saída é um arquivo PDF; impressoras reais: 8 bits
        mono = printer.outputFormat() == QPrinter.OutputFormat.PdfFormat
        factor = max(1, math.ceil(dpi / MAX_RENDER_DPI))
        page = printer.pageRect(QPrinter.Unit.DevicePixel)
        total = len(images)

        for i, img in enumerate(images):
            if i > 0:
                printer.newPage()
            target_w, target_h = compute_target(
                img.width, img.height, page.width(), page.height(), dpi)
            w = max(1, round(target_w / factor))
            h = max(1, round(target_h / factor))
            qimg = pil_to_bilevel_qimage(img, w, h, mono)
            painter.drawImage(QRectF(page.left(), page.top(), w * factor, h * factor), qimg)
            if progress:
                progress(i + 1, total)
    finally:
        painter.end()
