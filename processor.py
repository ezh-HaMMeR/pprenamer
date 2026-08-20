from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4

from config import AppSettings, app_base_dir
from extractor import (
    PaymentData,
    clean_recipient_name,
    extract_amount,
    extract_payment_date,
    extract_payment_number,
    extract_payer,
    extract_payer_inn,
    extract_recipient,
    extract_recipient_inn,
    extract_text_from_pdf,
    format_amount,
    normalize_lines,
)


@dataclass(frozen=True)
class ProcessResult:
    source: Path
    destination: Path | None
    ok: bool
    message: str


LogCallback = Callable[[str], None]
StatusCallback = Callable[[str], None]


SUPPORTED_PDF_EXTENSIONS = {".pdf"}
SUPPORTED_ZIP_EXTENSIONS = {".zip"}
SUPPORTED_7Z_EXTENSIONS = {".7z"}
SUPPORTED_RAR_EXTENSIONS = {".rar"}
SUPPORTED_ARCHIVE_EXTENSIONS = SUPPORTED_ZIP_EXTENSIONS | SUPPORTED_7Z_EXTENSIONS | SUPPORTED_RAR_EXTENSIONS
MAX_ARCHIVE_DEPTH = 10
ERROR_TOKEN = "[ERROR]"
KNOWN_PATTERN_FIELDS = {"number", "recipient", "amount", "date", "payer", "recipient_inn", "payer_inn"}



def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Windows не любит точку или пробел в конце имени файла.
    return name.rstrip(". ")


def format_pattern_value(field: str, value: str, settings: AppSettings) -> str:
    if not value or value == ERROR_TOKEN:
        return ERROR_TOKEN

    if field in {"recipient", "payer"}:
        return clean_recipient_name(
            value,
            title_case_person_names=settings.title_case_person_names,
        )

    if field == "amount":
        try:
            return format_amount(value, spaces=settings.normalize_amount_spaces)
        except Exception:
            return ERROR_TOKEN

    return value


def extract_pattern_fields(pattern: str) -> set[str]:
    # Находит простые переменные Python-формата: {number}, {recipient} и т.д.
    # Модификаторы форматирования игнорируются: {amount:>10} -> amount.
    fields = set()
    for match in re.finditer(r"(?<!{){([a-zA-Z_][a-zA-Z0-9_]*)(?:[^{}]*)}", pattern):
        fields.add(match.group(1))
    return fields


def render_filename(pattern: str, data: PaymentData, settings: AppSettings) -> str:
    values = {
        "number": format_pattern_value("number", data.number, settings),
        "recipient": format_pattern_value("recipient", data.recipient, settings),
        "amount": format_pattern_value("amount", data.amount, settings),
        "date": format_pattern_value("date", data.date, settings),
        "payer": format_pattern_value("payer", data.payer, settings),
        "recipient_inn": format_pattern_value("recipient_inn", data.recipient_inn, settings),
        "payer_inn": format_pattern_value("payer_inn", data.payer_inn, settings),
    }

    # Неизвестные переменные не должны ломать обработку.
    # Они заменяются на [ERROR], а файл уходит в папку пропущенных/проблемных файлов.
    for field in extract_pattern_fields(pattern) - KNOWN_PATTERN_FIELDS:
        values[field] = ERROR_TOKEN

    try:
        filename = pattern.format(**values)
    except Exception:
        filename = f"N {values['number']} - {values['recipient']} - {values['amount']} руб.pdf"

    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    return safe_filename(filename)


def resolve_skipped_dir(output_dir: Path, settings: AppSettings) -> Path:
    if settings.skipped_dir.strip():
        return Path(settings.skipped_dir).expanduser()
    return output_dir / "Пропущено"


def same_directory(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except Exception:
        return left.expanduser().absolute() == right.expanduser().absolute()


def apply_error_prefix_if_needed(filename: str, output_dir: Path, skipped_dir: Path, settings: AppSettings) -> str:
    prefix = getattr(settings, "error_file_prefix", "!_")
    if prefix and same_directory(output_dir, skipped_dir) and not filename.startswith(prefix):
        return safe_filename(prefix + filename)
    return filename


def extract_payment_data_for_filename(pdf_path: Path, pattern: str) -> tuple[PaymentData, list[str]]:
    errors: list[str] = []
    pattern_fields = extract_pattern_fields(pattern)
    unknown_fields = sorted(pattern_fields - KNOWN_PATTERN_FIELDS)
    for field in unknown_fields:
        errors.append(f"неизвестная переменная паттерна: {{{field}}}")

    try:
        text = extract_text_from_pdf(pdf_path)
        lines = normalize_lines(text)
    except Exception as exc:
        errors.append(f"не удалось прочитать PDF: {exc}")
        return PaymentData(
            number=ERROR_TOKEN,
            recipient=ERROR_TOKEN,
            amount=ERROR_TOKEN,
            date=ERROR_TOKEN if "date" in pattern_fields else "",
            payer=ERROR_TOKEN if "payer" in pattern_fields else "",
            recipient_inn=ERROR_TOKEN if "recipient_inn" in pattern_fields else "",
            payer_inn=ERROR_TOKEN if "payer_inn" in pattern_fields else "",
        ), errors

    def get_required(field: str, label: str, extractor) -> str:  # type: ignore[no-untyped-def]
        try:
            value = extractor()
            if not value:
                raise ValueError("пустое значение")
            return value
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            return ERROR_TOKEN

    number = get_required("number", "номер", lambda: extract_payment_number(text))
    recipient = get_required("recipient", "получатель", lambda: extract_recipient(lines, text))
    amount = get_required("amount", "сумма", lambda: extract_amount(lines, text))

    # Необязательные поля считаются ошибкой только тогда, когда они реально используются в паттерне имени.
    try:
        date = extract_payment_date(text)
    except Exception:
        date = ""
    if "date" in pattern_fields and not date:
        errors.append("дата: не найдена дата платежного поручения")
        date = ERROR_TOKEN

    try:
        payer = extract_payer(lines)
    except Exception:
        payer = ""
    if "payer" in pattern_fields and not payer:
        errors.append("плательщик: не найден плательщик")
        payer = ERROR_TOKEN

    try:
        recipient_inn = extract_recipient_inn(lines, text)
    except Exception:
        recipient_inn = ""
    if "recipient_inn" in pattern_fields and not recipient_inn:
        errors.append("ИНН получателя: не найден ИНН получателя")
        recipient_inn = ERROR_TOKEN

    try:
        payer_inn = extract_payer_inn(lines, text)
    except Exception:
        payer_inn = ""
    if "payer_inn" in pattern_fields and not payer_inn:
        errors.append("ИНН плательщика: не найден ИНН плательщика")
        payer_inn = ERROR_TOKEN

    return PaymentData(
        number=number,
        recipient=recipient,
        amount=amount,
        date=date,
        payer=payer,
        recipient_inn=recipient_inn,
        payer_inn=payer_inn,
    ), errors


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    counter = 2

    while True:
        candidate = path.with_name(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def resolve_collision(path: Path, strategy: str) -> Path | None:
    if not path.exists():
        return path
    if strategy == "overwrite":
        return path
    if strategy == "skip":
        return None
    return unique_path(path)


def is_archive(path: Path, settings: AppSettings) -> bool:
    ext = path.suffix.lower()
    return (
        (settings.process_zip and ext in SUPPORTED_ZIP_EXTENSIONS)
        or (settings.process_7z and ext in SUPPORTED_7Z_EXTENSIONS)
        or (settings.process_rar and ext in SUPPORTED_RAR_EXTENSIONS)
    )


def iter_directory_items(path: Path, settings: AppSettings) -> Iterable[Path]:
    return path.rglob("*") if settings.recursive_dirs else path.glob("*")


def safe_extract_zip(archive_path: Path, target_dir: Path) -> None:
    """Распаковывает ZIP с защитой от путей вида ../ внутри архива."""
    target_root = target_dir.resolve()
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            member_path = target_root / member.filename
            resolved = member_path.resolve()
            if not str(resolved).startswith(str(target_root)):
                raise RuntimeError(f"опасный путь внутри ZIP: {member.filename}")
        archive.extractall(target_root)


def find_7zip_executable() -> str | None:
    candidates = [
        "7z",
        "7za",
        "7zr",
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    for candidate in candidates:
        found = shutil.which(candidate) if not candidate.lower().endswith(".exe") else candidate
        if found and Path(found).exists():
            return str(found)
    return None


def extract_with_7zip(archive_path: Path, target_dir: Path) -> None:
    exe = find_7zip_executable()
    if not exe:
        raise RuntimeError("не найден 7-Zip. Установите 7-Zip или добавьте 7z.exe в PATH")

    command = [exe, "x", str(archive_path), f"-o{target_dir}", "-y"]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if completed.returncode != 0:
        tail = completed.stdout.strip()[-1000:]
        raise RuntimeError(f"7-Zip не смог распаковать архив: {tail}")


def extract_7z_archive(archive_path: Path, target_dir: Path) -> None:
    try:
        import py7zr  # type: ignore

        with py7zr.SevenZipFile(archive_path, mode="r") as archive:
            archive.extractall(target_dir)
        return
    except ImportError:
        # Переходим на внешний 7-Zip.
        extract_with_7zip(archive_path, target_dir)
    except Exception as py7zr_error:
        # Некоторые варианты 7z лучше обрабатываются установленным 7-Zip.
        try:
            extract_with_7zip(archive_path, target_dir)
        except Exception as seven_zip_error:
            raise RuntimeError(f"не удалось распаковать 7z: {py7zr_error}; 7-Zip: {seven_zip_error}") from py7zr_error


def extract_rar_archive(archive_path: Path, target_dir: Path) -> None:
    """
    Распаковывает RAR.

    Предпочтительный способ: пакет rarfile и доступный backend unrar/bsdtar/unar.
    Резервный способ: установленный 7-Zip, обычно это самый удобный вариант в Windows.
    """
    try:
        import rarfile  # type: ignore

        with rarfile.RarFile(archive_path) as archive:
            archive.extractall(target_dir)
        return
    except ImportError:
        extract_with_7zip(archive_path, target_dir)
    except Exception as rar_error:
        try:
            extract_with_7zip(archive_path, target_dir)
        except Exception as seven_zip_error:
            raise RuntimeError(
                "не удалось распаковать RAR. Установите 7-Zip/WinRAR/unrar. "
                f"RAR backend: {rar_error}; 7-Zip: {seven_zip_error}"
            ) from rar_error


def extract_archive(path: Path, temp_root: Path, settings: AppSettings, log: LogCallback | None = None) -> Path:
    ext = path.suffix.lower()
    target_dir = temp_root / f"{safe_filename(path.stem)}_{uuid4().hex[:8]}"
    target_dir.mkdir(parents=True, exist_ok=True)

    if ext == ".zip" and settings.process_zip:
        safe_extract_zip(path, target_dir)
        if log:
            log(f"[ARCHIVE] Распакован ZIP: {path.name}")
        return target_dir

    if ext == ".7z" and settings.process_7z:
        extract_7z_archive(path, target_dir)
        if log:
            log(f"[ARCHIVE] Распакован 7Z: {path.name}")
        return target_dir

    if ext == ".rar" and settings.process_rar:
        extract_rar_archive(path, target_dir)
        if log:
            log(f"[ARCHIVE] Распакован RAR: {path.name}")
        return target_dir

    raise RuntimeError(f"неподдерживаемый или отключенный архив: {path.name}")


def collect_pdf_files(
    input_paths: list[Path],
    settings: AppSettings,
    temp_root: Path,
    log: LogCallback | None = None,
) -> list[Path]:
    result: list[Path] = []
    seen_paths: set[Path] = set()

    # При сканировании распакованных архивов принудительно включаем рекурсивный поиск внутри распакованной папки.
    archive_scan_settings = replace(settings, recursive_dirs=True)

    def add_pdf(pdf_path: Path) -> None:
        resolved = pdf_path.resolve()
        if resolved not in seen_paths:
            seen_paths.add(resolved)
            result.append(pdf_path)

    def scan_path(path: Path, depth: int = 0, from_archive: bool = False) -> None:
        if depth > MAX_ARCHIVE_DEPTH:
            if log:
                log(f"[WARN] Слишком глубокая вложенность архивов, пропущено: {path.name}")
            return

        try:
            path = path.resolve()
        except Exception:
            pass

        if not path.exists():
            if log:
                log(f"[WARN] Путь не найден: {path}")
            return

        if path.is_dir():
            effective_settings = archive_scan_settings if from_archive else settings
            before = len(result)
            for item in iter_directory_items(path, effective_settings):
                if not item.is_file():
                    continue
                ext = item.suffix.lower()
                if ext in SUPPORTED_PDF_EXTENSIONS:
                    add_pdf(item)
                elif ext in SUPPORTED_ARCHIVE_EXTENSIONS and is_archive(item, settings):
                    if settings.recursive_archives:
                        scan_path(item, depth + 1, from_archive=from_archive)
                    elif log:
                        log(f"[SKIP] Архив внутри папки пропущен настройками: {item.name}")
            if log:
                found = len(result) - before
                label = "ARCHIVE-DIR" if from_archive else "DIR"
                log(f"[{label}] {path} → PDF добавлено: {found}")
            return

        if path.is_file() and path.suffix.lower() in SUPPORTED_PDF_EXTENSIONS:
            add_pdf(path)
            return

        if path.is_file() and is_archive(path, settings):
            target_dir = extract_archive(path, temp_root, settings, log=log)
            before = len(result)
            if settings.recursive_archives:
                scan_path(target_dir, depth + 1, from_archive=True)
            else:
                # Даже если рекурсивная обработка архивов отключена, все равно собираем PDF, лежащие прямо внутри распакованных папок.
                for item in target_dir.glob("*"):
                    if item.is_file() and item.suffix.lower() in SUPPORTED_PDF_EXTENSIONS:
                        add_pdf(item)
            if log:
                log(f"[ARCHIVE] {path.name} → PDF добавлено: {len(result) - before}")
            return

        if log:
            if path.is_file() and path.suffix.lower() in SUPPORTED_ARCHIVE_EXTENSIONS:
                log(f"[SKIP] Архив отключен в настройках: {path.name}")
            else:
                log(f"[SKIP] Не PDF/папка/поддерживаемый архив: {path.name}")

    for input_path in input_paths:
        scan_path(input_path)

    return result


def write_log_line(settings: AppSettings, line: str) -> None:
    if not settings.write_log_file:
        return
    try:
        log_path = app_base_dir() / settings.log_filename
        with log_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
    except Exception:
        # Ошибка логирования не должна прерывать пакетную обработку.
        pass


def copy_to_destination(pdf_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf_path, destination)


def process_one_pdf(pdf_path: Path, output_dir: Path, settings: AppSettings) -> ProcessResult:
    data, extraction_errors = extract_payment_data_for_filename(pdf_path, settings.filename_pattern)
    filename = render_filename(settings.filename_pattern, data, settings)

    if extraction_errors or ERROR_TOKEN in filename:
        if ERROR_TOKEN in filename and not extraction_errors:
            extraction_errors.append("одно из полей паттерна не удалось корректно сформировать")
        skipped_dir = resolve_skipped_dir(output_dir, settings)
        filename = apply_error_prefix_if_needed(filename, output_dir, skipped_dir, settings)
        destination = unique_path(skipped_dir / filename)
        copy_to_destination(pdf_path, destination)
        return ProcessResult(
            source=pdf_path,
            destination=destination,
            ok=False,
            message=(
                f"{pdf_path.name} → {destination.name} "
                f"(скопировано в Пропущено; { '; '.join(extraction_errors) })"
            ),
        )

    destination = output_dir / filename
    destination = resolve_collision(destination, settings.collision_strategy)

    if destination is None:
        return ProcessResult(
            source=pdf_path,
            destination=None,
            ok=False,
            message="файл с таким именем уже существует, пропущено",
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    if settings.copy_mode == "move":
        if settings.collision_strategy == "overwrite" and destination.exists():
            destination.unlink()
        shutil.move(str(pdf_path), str(destination))
    else:
        if settings.collision_strategy == "overwrite" and destination.exists():
            destination.unlink()
        shutil.copy2(pdf_path, destination)

    return ProcessResult(
        source=pdf_path,
        destination=destination,
        ok=True,
        message=f"{pdf_path.name} → {destination.name}",
    )


def process_inputs(
    input_paths: list[Path],
    settings: AppSettings,
    log: LogCallback | None = None,
    status: StatusCallback | None = None,
) -> list[ProcessResult]:
    output_dir = Path(settings.output_dir).expanduser()
    if not output_dir:
        raise ValueError("не указана папка результата")

    results: list[ProcessResult] = []

    def emit(line: str) -> None:
        if log:
            log(line)
        write_log_line(settings, line)

    with tempfile.TemporaryDirectory(prefix="payment_renamer_", ignore_cleanup_errors=True) as temp_dir:
        pdf_files = collect_pdf_files(input_paths, settings, Path(temp_dir), log=emit)
        total = len(pdf_files)

        if total == 0:
            emit("[INFO] PDF-файлы не найдены.")
            if status:
                status("PDF-файлы не найдены")
            return results

        emit(f"[INFO] Найдено PDF: {total}")

        for index, pdf_path in enumerate(pdf_files, start=1):
            if status:
                status(f"Обработка {index}/{total}: {pdf_path.name}")

            try:
                result = process_one_pdf(pdf_path, output_dir, settings)
                results.append(result)
                if result.ok:
                    prefix = "[OK]"
                elif result.destination is not None:
                    prefix = "[ERROR]"
                else:
                    prefix = "[SKIP]"
                emit(f"{prefix} {result.message}")
            except Exception as exc:
                result = ProcessResult(
                    source=pdf_path,
                    destination=None,
                    ok=False,
                    message=str(exc),
                )
                results.append(result)
                emit(f"[ERROR] {pdf_path.name}: {exc}")

        ok_count = sum(1 for item in results if item.ok)
        error_count = len(results) - ok_count
        final = f"Готово: успешно {ok_count}, ошибок/пропусков {error_count}"
        emit(f"[DONE] {final}")
        if status:
            status(final)

    return results


def open_folder(path: str) -> None:
    folder = Path(path).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(folder)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(folder)])
