"""
printing.py

Impressão das etiquetas usando o sistema de impressão nativo do Qt
(QtPrintSupport), que funciona em Windows (drivers do Windows), macOS e
Linux (CUPS) e permite escolher entre várias impressoras.

Cada etiqueta é enviada como uma página 10x15 cm, sem margens, alinhada ao
topo-esquerda (mesma lógica do PDF).
"""

from PySide6.QtCore import QRectF, QSizeF
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter
from PySide6.QtPrintSupport import QPrinter

LABEL_WIDTH_MM = 100.0
LABEL_HEIGHT_MM = 150.0


def apply_label_page(printer: QPrinter):
    """Configura a impressora para papel 100x150 mm, retrato, sem margens."""
    printer.setPageSize(QPageSize(QSizeF(LABEL_WIDTH_MM, LABEL_HEIGHT_MM),
                                  QPageSize.Unit.Millimeter))
    printer.setPageOrientation(QPageLayout.Orientation.Portrait)
    printer.setFullPage(True)


def make_printer() -> QPrinter:
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    apply_label_page(printer)
    return printer


def pil_to_qimage(img) -> QImage:
    rgb = img.convert("RGB")
    w, h = rgb.size
    # .copy() desvincula o QImage do buffer de bytes temporário
    return QImage(rgb.tobytes("raw", "RGB"), w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def print_images(images, printer: QPrinter):
    """Imprime as imagens (PIL) na impressora, uma etiqueta por página.

    O diálogo de impressão pode trocar o tamanho de papel para o padrão da
    impressora escolhida, por isso o tamanho 10x15 é reaplicado aqui.
    """
    if not images:
        raise ValueError("Não há etiquetas para imprimir.")

    apply_label_page(printer)

    painter = QPainter()
    if not painter.begin(printer):
        raise RuntimeError(
            "Não foi possível iniciar a impressão. Verifique se a impressora "
            "está ligada e disponível."
        )
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for i, img in enumerate(images):
            if i > 0:
                printer.newPage()
            qimg = pil_to_qimage(img)
            page = printer.pageRect(QPrinter.Unit.DevicePixel)
            scale = min(page.width() / qimg.width(), page.height() / qimg.height())
            target = QRectF(page.left(), page.top(),
                            qimg.width() * scale, qimg.height() * scale)
            painter.drawImage(target, qimg)
    finally:
        painter.end()
