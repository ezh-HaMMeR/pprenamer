from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import AppSettings, load_settings, save_settings
from processor import open_folder, process_inputs


def resource_path(relative_path: str) -> str:
    """Возвращает путь к ресурсу при запуске из исходников и в режиме PyInstaller onefile."""
    if hasattr(sys, "_MEIPASS"):
        return str(Path(sys._MEIPASS) / relative_path)
    return str(Path(__file__).resolve().parent / relative_path)


class DropArea(QFrame):
    paths_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("DropArea")

        layout = QVBoxLayout(self)
        label = QLabel("Перетащите сюда PDF, папки или ZIP/7Z/RAR-архивы")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(label)

        hint = QLabel("Исходные файлы по умолчанию не меняются: переименованные копии сохраняются в папку результата")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("dragging", True)
            self.style().unpolish(self)
            self.style().polish(self)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self.setProperty("dragging", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self.setProperty("dragging", False)
        self.style().unpolish(self)
        self.style().polish(self)

        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(560)
        self.settings = settings

        self.copy_mode = QComboBox()
        self.copy_mode.addItem("Копировать исходники", "copy")
        self.copy_mode.addItem("Перемещать исходники", "move")
        self.copy_mode.setCurrentIndex(0 if settings.copy_mode == "copy" else 1)

        self.collision_strategy = QComboBox()
        self.collision_strategy.addItem("Добавлять (2), (3)...", "unique")
        self.collision_strategy.addItem("Перезаписывать", "overwrite")
        self.collision_strategy.addItem("Пропускать", "skip")
        for idx in range(self.collision_strategy.count()):
            if self.collision_strategy.itemData(idx) == settings.collision_strategy:
                self.collision_strategy.setCurrentIndex(idx)
                break

        self.skipped_dir = QLineEdit(settings.skipped_dir)
        self.skipped_dir.setPlaceholderText(r"Пусто = <папка результата>\Пропущено")
        skipped_dir_row = QHBoxLayout()
        skipped_dir_row.addWidget(self.skipped_dir, stretch=1)
        skipped_dir_browse_btn = QPushButton("...")
        skipped_dir_browse_btn.setFixedWidth(42)
        skipped_dir_row.addWidget(skipped_dir_browse_btn)
        self.skipped_dir_browse_btn = skipped_dir_browse_btn

        self.error_file_prefix = QLineEdit(settings.error_file_prefix)
        self.error_file_prefix.setPlaceholderText("Например: !_")

        self.recursive_dirs = QCheckBox("Обрабатывать PDF во вложенных папках")
        self.recursive_dirs.setChecked(settings.recursive_dirs)

        self.recursive_archives = QCheckBox("Рекурсивно искать PDF внутри архивов и вложенных архивов")
        self.recursive_archives.setChecked(settings.recursive_archives)

        self.process_zip = QCheckBox("Обрабатывать ZIP-архивы")
        self.process_zip.setChecked(settings.process_zip)

        self.process_7z = QCheckBox("Обрабатывать 7Z-архивы")
        self.process_7z.setChecked(settings.process_7z)

        self.process_rar = QCheckBox("Обрабатывать RAR-архивы, если установлен 7-Zip / WinRAR / unrar")
        self.process_rar.setChecked(settings.process_rar)

        self.normalize_amount_spaces = QCheckBox("Формат суммы с пробелами: 19 200-00")
        self.normalize_amount_spaces.setChecked(settings.normalize_amount_spaces)

        self.title_case_person_names = QCheckBox("ФИО в верхнем регистре приводить к нормальному виду")
        self.title_case_person_names.setChecked(settings.title_case_person_names)

        self.minimize_to_tray = QCheckBox("При сворачивании скрывать окно в трей")
        self.minimize_to_tray.setChecked(settings.minimize_to_tray)

        self.close_to_tray = QCheckBox("При закрытии скрывать окно в трей")
        self.close_to_tray.setChecked(settings.close_to_tray)

        self.write_log_file = QCheckBox("Писать лог-файл рядом с программой")
        self.write_log_file.setChecked(settings.write_log_file)

        self.log_filename = QLineEdit(settings.log_filename)

        form = QFormLayout()
        form.addRow("Режим работы с файлами:", self.copy_mode)
        form.addRow("Если файл уже существует:", self.collision_strategy)
        form.addRow("Папка для пропущенных:", skipped_dir_row)
        form.addRow("Префикс файлов с ошибками:", self.error_file_prefix)
        form.addRow("", self.recursive_dirs)
        form.addRow("", self.recursive_archives)
        form.addRow("", self.process_zip)
        form.addRow("", self.process_7z)
        form.addRow("", self.process_rar)
        form.addRow("", self.normalize_amount_spaces)
        form.addRow("", self.title_case_person_names)
        form.addRow("", self.minimize_to_tray)
        form.addRow("", self.close_to_tray)
        form.addRow("", self.write_log_file)
        form.addRow("Имя лог-файла:", self.log_filename)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Сохранить")
        cancel_btn = QPushButton("Отмена")
        buttons.addStretch(1)
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

        save_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        self.skipped_dir_browse_btn.clicked.connect(self.choose_skipped_dir)

    def choose_skipped_dir(self) -> None:
        start = (
            self.skipped_dir.text().strip()
            or self.settings.output_dir
            or str(Path.home() / "Desktop")
        )
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для пропущенных платежек", start)
        if folder:
            self.skipped_dir.setText(folder)

    def to_settings(self) -> AppSettings:
        return AppSettings(
            settings_version=self.settings.settings_version,
            output_dir=self.settings.output_dir,
            skipped_dir=self.skipped_dir.text().strip(),
            error_file_prefix=self.error_file_prefix.text(),
            filename_pattern=self.settings.filename_pattern,
            copy_mode=self.copy_mode.currentData(),
            recursive_dirs=self.recursive_dirs.isChecked(),
            recursive_archives=self.recursive_archives.isChecked(),
            process_zip=self.process_zip.isChecked(),
            process_7z=self.process_7z.isChecked(),
            process_rar=self.process_rar.isChecked(),
            collision_strategy=self.collision_strategy.currentData(),
            normalize_amount_spaces=self.normalize_amount_spaces.isChecked(),
            title_case_person_names=self.title_case_person_names.isChecked(),
            minimize_to_tray=self.minimize_to_tray.isChecked(),
            close_to_tray=self.close_to_tray.isChecked(),
            write_log_file=self.write_log_file.isChecked(),
            log_filename=self.log_filename.text().strip() or "payment_renamer.log",
        )


class Worker(QObject):
    status = Signal(str)
    log = Signal(str)
    finished = Signal()

    def __init__(self, paths: list[str], settings: AppSettings) -> None:
        super().__init__()
        self.paths = paths
        self.settings = settings

    def run(self) -> None:
        """
        Выполняется в отдельном QThread.

        Важно: нельзя позволять исключениям выходить за пределы этого метода.
        В собранном PySide/PyInstaller-приложении необработанное исключение
        в рабочем потоке может выглядеть так, будто приложение просто исчезло,
        особенно при запуске двойным кликом.
        """
        try:
            process_inputs(
                [Path(path) for path in self.paths],
                self.settings,
                log=self.log.emit,
                status=self.status.emit,
            )
        except BaseException as exc:  # noqa: BLE001 - это граница рабочего потока GUI
            details = traceback.format_exc()
            self.log.emit(f"[FATAL] {exc}")
            self.log.emit(details)
            self.status.emit(f"Ошибка: {exc}")
        finally:
            self.finished.emit()

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self.processing_active = False
        self.exit_requested = False

        self.setWindowTitle("PP Renamer v0.2b")
        self.setMinimumSize(860, 560)

        self.drop_area = DropArea()
        self.output_edit = QLineEdit(self.settings.output_dir)
        self.pattern_edit = QLineEdit(self.settings.filename_pattern)
        self.status_edit = QLineEdit("Готов к работе")
        self.status_edit.setReadOnly(True)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(150)

        self._build_ui()
        self._build_tray()
        self._connect_signals()
        self._apply_styles()

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addWidget(self.drop_area, stretch=1)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Куда сохранять:"))
        output_row.addWidget(self.output_edit, stretch=1)
        browse_btn = QPushButton("...")
        browse_btn.setFixedWidth(42)
        output_row.addWidget(browse_btn)
        self.browse_btn = browse_btn

        pattern_row = QHBoxLayout()
        pattern_row.addWidget(QLabel("Паттерн имени:"))
        pattern_row.addWidget(self.pattern_edit, stretch=1)
        pattern_help_btn = QPushButton("?")
        pattern_help_btn.setFixedWidth(32)
        pattern_help_btn.setToolTip("Показать доступные переменные для паттерна имени")
        pattern_row.addWidget(pattern_help_btn)
        self.pattern_help_btn = pattern_help_btn

        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Статус:"))
        status_row.addWidget(self.status_edit, stretch=1)

        button_row = QHBoxLayout()
        self.settings_btn = QPushButton("Настройки")
        self.open_folder_btn = QPushButton("Открыть папку результата")
        self.clear_log_btn = QPushButton("Очистить лог")
        button_row.addWidget(self.settings_btn)
        button_row.addWidget(self.open_folder_btn)
        button_row.addWidget(self.clear_log_btn)
        button_row.addStretch(1)

        layout.addLayout(output_row)
        layout.addLayout(pattern_row)
        layout.addLayout(status_row)
        layout.addLayout(button_row)
        layout.addWidget(QLabel("Лог:"))
        layout.addWidget(self.log_view)

        feedback_label = QLabel(
            'Сообщить о найденном баге / предложить фичу: '
            '<a href="https://t.me/freaksty1e">https://t.me/freaksty1e</a>'
        )
        feedback_label.setOpenExternalLinks(True)
        feedback_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        feedback_label.setToolTip("Открыть Telegram для обратной связи")
        layout.addWidget(feedback_label)
        self.feedback_label = feedback_label

        self.setCentralWidget(central)

    def _build_tray(self) -> None:
        app_icon = QIcon(resource_path("app.ico"))
        if app_icon.isNull():
            app_icon = self.style().standardIcon(self.style().StandardPixmap.SP_FileDialogDetailedView)

        self.setWindowIcon(app_icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(app_icon)

        self.tray = QSystemTrayIcon(app_icon, self)
        self.tray.setToolTip("PP Renamer v0.1b")

        menu = QMenu()
        show_action = QAction("Показать", self)
        open_action = QAction("Открыть папку результата", self)
        quit_action = QAction("Выход", self)
        menu.addAction(show_action)
        menu.addAction(open_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        show_action.triggered.connect(self.show_from_tray)
        open_action.triggered.connect(self.open_output_folder)
        quit_action.triggered.connect(self.quit_app)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _connect_signals(self) -> None:
        self.drop_area.paths_dropped.connect(self.start_processing)
        self.browse_btn.clicked.connect(self.choose_output_dir)
        self.settings_btn.clicked.connect(self.open_settings)
        self.open_folder_btn.clicked.connect(self.open_output_folder)
        self.clear_log_btn.clicked.connect(self.log_view.clear)
        self.pattern_help_btn.clicked.connect(self.show_pattern_help)
        self.output_edit.textChanged.connect(self._save_quick_settings)
        self.pattern_edit.textChanged.connect(self._save_quick_settings)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QFrame#DropArea {
                border: 2px dashed #7d8790;
                border-radius: 12px;
                background: #f7f7f7;
                min-height: 190px;
            }
            QFrame#DropArea[dragging="true"] {
                border-color: #2d77d2;
                background: #eaf3ff;
            }
            QLineEdit[readOnly="true"] {
                background: #f1f1f1;
            }
            """
        )

    def show_pattern_help(self) -> None:
        QMessageBox.information(
            self,
            "Переменные паттерна имени",
            "Доступные переменные для строки «Паттерн имени»:\n\n"
            "{number} — номер платежного поручения\n"
            "{recipient} — получатель платежа\n"
            "{recipient_inn} — ИНН получателя платежа\n"
            "{amount} — сумма платежа в формате из настроек\n"
            "{date} — дата платежного поручения\n"
            "{payer} — плательщик, если его удалось извлечь\n"
            "{payer_inn} — ИНН плательщика\n\n"
            "Если используемое в паттерне поле не удалось прочитать, вместо него будет [ERROR], "
            "а файл будет скопирован в папку для пропущенных.\n\n"
            "Пример:\n"
            "N {number} - {recipient} - {amount} руб.pdf\n\n"
            "Результат:\n"
            "N 007 - ИП Пупкин Василий Васильевич - 10 322-00 руб.pdf",
        )

    def _save_quick_settings(self) -> None:
        self.settings.output_dir = self.output_edit.text().strip()
        self.settings.filename_pattern = self.pattern_edit.text().strip() or AppSettings.defaults().filename_pattern
        save_settings(self.settings)

    def choose_output_dir(self) -> None:
        start = self.output_edit.text().strip() or str(Path.home() / "Desktop")
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку результата", start)
        if folder:
            self.output_edit.setText(folder)

    def open_settings(self) -> None:
        self._save_quick_settings()
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.to_settings()
            save_settings(self.settings)
            self.output_edit.setText(self.settings.output_dir)
            self.pattern_edit.setText(self.settings.filename_pattern)
            self.add_log("[INFO] Настройки сохранены")

    def open_output_folder(self) -> None:
        self._save_quick_settings()
        if not self.settings.output_dir:
            QMessageBox.warning(self, "Папка не указана", "Сначала укажите папку результата.")
            return
        try:
            open_folder(self.settings.output_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", str(exc))

    def add_log(self, line: str) -> None:
        self.log_view.append(line)

    def set_status(self, line: str) -> None:
        self.status_edit.setText(line)

    def start_processing(self, paths: list[str]) -> None:
        if self.processing_active or (self.thread is not None and self.thread.isRunning()):
            QMessageBox.information(self, "Обработка уже идет", "Дождитесь завершения текущей обработки.")
            return

        self._save_quick_settings()
        if not self.settings.output_dir:
            QMessageBox.warning(self, "Папка не указана", "Укажите папку результата перед обработкой.")
            return

        self.add_log("-" * 80)
        self.add_log(f"[DROP] Получено объектов: {len(paths)}")
        self.set_status("Подготовка...")

        self.processing_active = True
        self.thread = QThread(self)
        self.worker = Worker(paths, self.settings)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.status.connect(self.set_status)
        self.worker.log.connect(self.add_log)

        # Корректный жизненный цикл потока Qt:
        # 1. worker отправляет finished из рабочего потока
        # 2. цикл событий thread получает команду завершиться
        # 3. worker удаляется в своем потоке через deleteLater
        # 4. Python-ссылки очищаются только после QThread.finished
        # Более ранняя очистка ссылок может случайно завершать GUI-сборки после
        # успешной обработки архивов в некоторых сочетаниях Windows/PySide.
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()

    def _thread_finished(self) -> None:
        self.processing_active = False
        self.worker = None
        self.thread = None
        if self.tray.isVisible():
            self.tray.showMessage(
                "PP Renamer v0.1b",
                self.status_edit.text(),
                QSystemTrayIcon.MessageIcon.Information,
                3500,
            )

    def show_from_tray(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Trigger):
            self.show_from_tray()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        if event.type() == event.Type.WindowStateChange:
            if self.isMinimized() and self.settings.minimize_to_tray:
                self.hide()
                self.tray.showMessage(
                    "PP Renamer v0.1b",
                    "Программа свернута в трей.",
                    QSystemTrayIcon.MessageIcon.Information,
                    2500,
                )
        super().changeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.exit_requested:
            event.accept()
            return

        if self.settings.close_to_tray:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "PP Renamer v0.1b",
                "Программа продолжает работать в трее. Для выхода используйте меню трея.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
        else:
            event.accept()

    def quit_app(self) -> None:
        if self.processing_active:
            QMessageBox.information(self, "Обработка идет", "Дождитесь завершения обработки перед выходом.")
            return
        self.exit_requested = True
        self.tray.hide()
        QApplication.quit()


def _install_global_exception_hook() -> None:
    def handle_exception(exc_type, exc_value, exc_traceback):
        message = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        try:
            from config import app_base_dir
            crash_log = app_base_dir() / "payment_renamer_crash.log"
            crash_log.write_text(message, encoding="utf-8")
        except Exception:
            pass
        try:
            QMessageBox.critical(None, "Критическая ошибка", message[-4000:])
        except Exception:
            pass

    sys.excepthook = handle_exception


def run_app() -> int:
    _install_global_exception_hook()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    return app.exec()
