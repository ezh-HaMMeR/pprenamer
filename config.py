from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SETTINGS_VERSION = 4


def app_base_dir() -> Path:
    """
    Папка, в которой будет храниться settings.json.

    При запуске из исходников это папка проекта.
    В режиме PyInstaller EXE это папка, где расположен EXE-файл.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


@dataclass
class AppSettings:
    settings_version: int = SETTINGS_VERSION
    output_dir: str = ""
    skipped_dir: str = ""  # пусто = <папка результата>/Пропущено
    error_file_prefix: str = "!_"  # добавляется к ошибочным файлам, если output_dir == skipped_dir
    filename_pattern: str = "N {number} - {recipient} - {amount} руб.pdf"
    copy_mode: str = "copy"  # copy = копировать | move = перемещать
    recursive_dirs: bool = True
    recursive_archives: bool = True
    process_zip: bool = True
    process_7z: bool = True
    process_rar: bool = True
    collision_strategy: str = "unique"  # unique = добавлять номер | overwrite = перезаписывать | skip = пропускать
    normalize_amount_spaces: bool = True
    title_case_person_names: bool = True
    minimize_to_tray: bool = True
    close_to_tray: bool = True
    write_log_file: bool = True
    log_filename: str = "payment_renamer.log"

    @classmethod
    def defaults(cls) -> "AppSettings":
        return cls()


def settings_path() -> Path:
    return app_base_dir() / "settings.json"


def load_settings() -> AppSettings:
    path = settings_path()
    defaults = AppSettings.defaults()

    if not path.exists():
        return defaults

    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return defaults

    old_version = int(data.get("settings_version", 1) or 1)

    allowed = set(asdict(defaults).keys())
    clean = {key: value for key, value in data.items() if key in allowed}
    settings = AppSettings(**{**asdict(defaults), **clean})

    # Миграция из первого прототипа: в v1 7z был отключен по умолчанию, а RAR еще не поддерживался.
    # Для нового сценария работы с архивами включаем обработку архивов, если пользователь потом не изменит настройку.
    if old_version < SETTINGS_VERSION:
        settings.settings_version = SETTINGS_VERSION
        settings.process_7z = True
        settings.process_rar = True
        settings.recursive_archives = True
        # v3: добавлена настраиваемая папка для проблемных/пропущенных файлов. Пустое значение означает папку по умолчанию.
        if not hasattr(settings, "skipped_dir"):
            settings.skipped_dir = ""
        # v4: добавлен префикс ошибок и переменные ИНН в паттерне имени файла.
        if not hasattr(settings, "error_file_prefix"):
            settings.error_file_prefix = "!_"
        try:
            save_settings(settings)
        except Exception:
            pass

    return settings


def save_settings(settings: AppSettings) -> None:
    path = settings_path()
    settings.settings_version = SETTINGS_VERSION
    path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
