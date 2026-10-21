"""
main.py

Aplicativo desktop (Windows / macOS / Linux) para converter arquivos
ZPL (.zpl, .txt) ou pacotes .zip contendo etiquetas ZPL em um único PDF
pronto para impressão, no formato de etiqueta 10x15 cm.

Como rodar (com Python instalado):
    pip install -r requirements.txt
    python main.py
"""

import os
import subprocess
import sys
import traceback

from PySide6.QtCore import Signal, QThread
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QListWidget, QListWidgetItem, QFileDialog,
    QProgressBar, QMessageBox, QFrame, QComboBox, QTextEdit
)

from converter import convert_files_to_pdf

APP_TITLE = "ZPL/TXT/ZIP → PDF (Etiqueta 10x15)"
APP_VERSION = "1.0.0"
APP_AUTHOR = "Rodrigo Onori"  # troque pelo seu nome ou da sua empresa
ACCEPTED_EXT = (".zpl", ".txt", ".zip")


def resource_path(*parts):
    """Caminho de um recurso, funcionando rodando pelo Python e empacotado (.exe)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


class ConversionWorker(QThread):
    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    finished_error = Signal(str)

    def __init__(self, files, output_path, dpi):
        super().__init__()
        self.files = files
        self.output_path = output_path
        self.dpi = dpi

    def run(self):
        try:
            result = convert_files_to_pdf(
                self.files, self.output_path, dpi=self.dpi,
                progress_callback=lambda c, t, m: self.progress.emit(c, t, m),
            )
            self.finished_ok.emit(result)
        except Exception:
            self.finished_error.emit(traceback.format_exc())


class DropListWidget(QListWidget):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.ExtendedSelection)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local and local.lower().endswith(ACCEPTED_EXT):
                paths.append(local)
        if paths:
            self.files_dropped.emit(paths)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(680, 600)
        self.selected_files = []
        self.worker = None

        self._build_menu()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel(APP_TITLE)
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        subtitle = QLabel(
            "Arraste arquivos .zpl, .txt ou .zip aqui, ou use o botão abaixo.\n"
            "Cada etiqueta será convertida em uma página de PDF no tamanho 10x15 cm."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #555;")
        layout.addWidget(subtitle)

        self.list_widget = DropListWidget()
        self.list_widget.files_dropped.connect(self.add_files)
        self.list_widget.setMinimumHeight(180)
        self.list_widget.setStyleSheet(
            "QListWidget { border: 2px dashed #aaa; border-radius: 8px; padding: 8px; }"
        )
        layout.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Adicionar arquivos...")
        self.btn_add.clicked.connect(self.on_add_clicked)
        self.btn_remove = QPushButton("Remover selecionados")
        self.btn_remove.clicked.connect(self.on_remove_clicked)
        self.btn_clear = QPushButton("Limpar tudo")
        self.btn_clear.clicked.connect(self.on_clear_clicked)
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_remove)
        btn_row.addWidget(self.btn_clear)
        layout.addLayout(btn_row)

        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("Resolução da impressora (DPI):"))
        self.dpi_combo = QComboBox()
        self.dpi_combo.addItems(["203 dpi (mais comum)", "300 dpi", "600 dpi"])
        opt_row.addWidget(self.dpi_combo)
        opt_row.addStretch()
        layout.addLayout(opt_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        self.btn_convert = QPushButton("Converter para PDF")
        self.btn_convert.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: white; padding: 10px; "
            "font-weight: bold; border-radius: 6px; } "
            "QPushButton:disabled { background-color: #9ca3af; }"
        )
        self.btn_convert.clicked.connect(self.on_convert_clicked)
        layout.addWidget(self.btn_convert)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(120)
        self.log_box.setPlaceholderText("Avisos e mensagens aparecerão aqui...")
        layout.addWidget(self.log_box)

    # ------------------------------------------------------------------ #
    def _build_menu(self):
        help_menu = self.menuBar().addMenu("&Ajuda")

        about_action = QAction("&Sobre", self)
        about_action.setShortcut(QKeySequence("F1"))
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        # about_qt_action = QAction("Sobre o &Qt", self)
        # about_qt_action.triggered.connect(QApplication.aboutQt)
        # help_menu.addAction(about_qt_action)

    def show_about(self):
        QMessageBox.about(
            self,
            "Sobre",
            f"<h3>{APP_TITLE}</h3>"
            f"<p>Versão {APP_VERSION}</p>"
            "<p>Converte arquivos ZPL (.zpl, .txt e .zip) em PDF no formato "
            "de etiqueta 10x15 cm, pronto para impressão. Funciona 100% offline.</p>"
            "<p><b>Bibliotecas utilizadas:</b><br>"
            "PySide6 (interface), Pillow, ReportLab, python-barcode, qrcode "
            "e fonte DejaVu Sans Mono.</p>"
            f"<p>Desenvolvido por {APP_AUTHOR}.</p>"
        )

    # ------------------------------------------------------------------ #
    def dpi_value(self):
        return int(self.dpi_combo.currentText().split(" ")[0])

    def add_files(self, paths):
        for p in paths:
            if p not in self.selected_files:
                self.selected_files.append(p)
                self.list_widget.addItem(QListWidgetItem(os.path.basename(p) + "   —   " + p))

    def on_add_clicked(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Selecionar arquivos ZPL / TXT / ZIP", "",
            "Etiquetas (*.zpl *.txt *.zip);;Todos os arquivos (*.*)"
        )
        if paths:
            self.add_files(paths)

    def on_remove_clicked(self):
        rows = sorted((self.list_widget.row(i) for i in self.list_widget.selectedItems()),
                      reverse=True)
        for row in rows:
            self.list_widget.takeItem(row)
            self.selected_files.pop(row)

    def on_clear_clicked(self):
        self.list_widget.clear()
        self.selected_files.clear()

    def on_convert_clicked(self):
        if not self.selected_files:
            QMessageBox.warning(self, APP_TITLE, "Adicione pelo menos um arquivo .zpl, .txt ou .zip.")
            return

        output_path, _ = QFileDialog.getSaveFileName(
            self, "Salvar PDF como...", "etiquetas.pdf", "PDF (*.pdf)"
        )
        if not output_path:
            return
        if not output_path.lower().endswith(".pdf"):
            output_path += ".pdf"

        self.btn_convert.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log_box.clear()
        self.status_label.setText("Convertendo...")

        self.worker = ConversionWorker(list(self.selected_files), output_path, self.dpi_value())
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_ok.connect(self.on_finished_ok)
        self.worker.finished_error.connect(self.on_finished_error)
        self.worker.start()

    def on_progress(self, cur, total, msg):
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(cur)
        self.status_label.setText(msg)

    def on_finished_ok(self, result):
        self.btn_convert.setEnabled(True)
        self.progress_bar.setValue(self.progress_bar.maximum())

        if result.pdf_path:
            self.status_label.setText(
                f"Concluído! {result.total_labels} etiqueta(s) geradas em: {result.pdf_path}"
            )
        else:
            self.status_label.setText("Não foi possível gerar o PDF. Veja os avisos abaixo.")

        for w in result.warnings:
            self.log_box.append("⚠ " + w)

        if result.pdf_path:
            resp = QMessageBox.question(
                self, APP_TITLE,
                f"PDF gerado com sucesso!\n{result.total_labels} etiqueta(s).\n\nAbrir o arquivo agora?",
                QMessageBox.Yes | QMessageBox.No
            )
            if resp == QMessageBox.Yes:
                self.open_file(result.pdf_path)

    def on_finished_error(self, error_text):
        self.btn_convert.setEnabled(True)
        self.status_label.setText("Ocorreu um erro durante a conversão.")
        self.log_box.append("ERRO:\n" + error_text)
        QMessageBox.critical(self, APP_TITLE, "Ocorreu um erro durante a conversão. Veja o log na janela.")

    @staticmethod
    def open_file(path):
        try:
            if sys.platform.startswith("darwin"):
                subprocess.Popen(["open", path])
            elif sys.platform.startswith("win"):
                os.startfile(path)  # noqa
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(resource_path("assets/imgs", "icon.png")))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()