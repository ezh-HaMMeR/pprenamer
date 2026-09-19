from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import fitz  # библиотека PyMuPDF


@dataclass(frozen=True)
class PaymentData:
    number: str
    recipient: str
    amount: str
    date: str = ""
    payer: str = ""
    recipient_inn: str = ""
    payer_inn: str = ""


class ExtractError(ValueError):
    pass


# Суммы могут быть записаны как 19200-00, 19200,00 или 19200.00.
# Для значений с точкой должно быть минимум 3 цифры до точки, чтобы не
# принимать фрагменты даты вроде 19.08 за сумму.
MONEY_RE = re.compile(r"\b(?:\d{1,12}[-,]\d{2}|\d{3,12}\.\d{2})\b")


def extract_text_from_pdf(pdf_path: Path) -> str:
    parts: list[str] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            parts.append(page.get_text("text"))
    return "\n".join(parts)


def normalize_lines(text: str) -> list[str]:
    text = text.replace("\xa0", " ")
    lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def extract_payment_number(text: str) -> str:
    """Извлекает номер платежного поручения только из той же строки, где находится заголовок документа.

    Важно: здесь нельзя использовать \\s после знака №. В регулярных выражениях \\s
    также захватывает перенос строки, поэтому при пустом номере можно случайно взять
    значение со следующей строки, например из даты 19.08.2026.
    """
    title_re = re.compile(r"ПЛАТ[ЕЁ]ЖНОЕ\s+ПОРУЧЕНИЕ\s*№", flags=re.IGNORECASE)
    number_re = re.compile(r"ПЛАТ[ЕЁ]ЖНОЕ\s+ПОРУЧЕНИЕ\s*№[ \t]*(\d+)", flags=re.IGNORECASE)

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line.replace("\xa0", " ")).strip()
        if not line:
            continue
        if title_re.search(line):
            match = number_re.search(line)
            if match:
                return match.group(1)
            raise ExtractError("не найден номер платежного поручения")

    raise ExtractError("не найден номер платежного поручения")


def extract_payment_date(text: str) -> str:
    """Извлекает дату самого платежного поручения, а не дату выгрузки PDF."""
    header_re = re.compile(r"ПЛАТ[ЕЁ]ЖНОЕ\s+ПОРУЧЕНИЕ\s*№", flags=re.IGNORECASE)
    date_re = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
    lines = normalize_lines(text)

    header_index: int | None = None
    for index, line in enumerate(lines):
        if header_re.search(line):
            header_index = index
            dates = date_re.findall(line)
            if dates:
                return dates[-1]
            break

    if header_index is not None:
        # В PDF СберБизнес дата документа обычно извлекается сразу после
        # заголовка, а дата формирования/выгрузки находится до него.
        for line in lines[header_index + 1 : header_index + 10]:
            dates = date_re.findall(line)
            if dates:
                return dates[0]
            if line.lower() in {"сумма", "сумма прописью"}:
                break

        # В некоторых вариантах верстки значение даты идет непосредственно
        # перед подписью поля "Дата".
        for index in range(header_index + 1, min(len(lines), header_index + 15)):
            if lines[index].lower() != "дата":
                continue
            for candidate in reversed(lines[header_index + 1 : index]):
                dates = date_re.findall(candidate)
                if dates:
                    return dates[-1]

    # Резерв для неизвестных форматов без распознанного заголовка.
    dates = date_re.findall(text)
    return dates[0] if dates else ""


def inn_values(line: str) -> list[str]:
    """Возвращает значения ИНН, записанные как 'ИНН 1234567890/123456789012'."""
    return re.findall(r"\bИНН\s*(\d{10}|\d{12})\b", line, flags=re.IGNORECASE)


def inn_values_in_range(lines: list[str], start: int, end: int) -> list[str]:
    """Возвращает значения ИНН из диапазона строк, включая разнесенную верстку.

    Некоторые PDF извлекаются как две строки:
        ИНН
        182909079132
    а некоторые — как одна строка:
        ИНН 182909079132 КПП
    Этот помощник поддерживает оба варианта, но все равно требует явную метку ИНН.
    """
    result: list[str] = []
    safe_start = max(0, start)
    safe_end = min(len(lines), end)

    for index in range(safe_start, safe_end):
        line = lines[index]
        result.extend(inn_values(line))

        if re.search(r"\bИНН\b", line, flags=re.IGNORECASE):
            # Разнесенная верстка: 'ИНН' находится в текущей строке, а номер рядом.
            for offset in range(1, 4):
                next_index = index + offset
                if next_index >= safe_end:
                    break
                candidate = lines[next_index].strip()
                if re.fullmatch(r"\d{10}|\d{12}", candidate):
                    result.append(candidate)
                    break
                # Останавливаемся, если следующее поле началось раньше, чем был найден номер.
                if candidate.lower() in {"кпп", "сумма", "сч. №", "плательщик", "получатель"}:
                    break

        # Другой вариант разметки PyMuPDF может дать только строку с номером, при этом
        # соседняя строка содержит 'КПП'. Мы принимаем это, потому что ИНН
        # состоит ровно из 10 или 12 цифр, в отличие от счетов и БИК.
        if re.fullmatch(r"\d{10}|\d{12}", line.strip()):
            previous_line = lines[index - 1].lower() if index > safe_start else ""
            next_line = lines[index + 1].lower() if index + 1 < safe_end else ""
            if "инн" in previous_line or "кпп" in next_line:
                result.append(line.strip())

    return result


def _find_first_line_index(lines: list[str], variants: tuple[str, ...]) -> int | None:
    for index, line in enumerate(lines):
        low = line.lower()
        if any(variant in low for variant in variants):
            return index
    return None


def _first_purpose_index(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        low = line.lower()
        if low.startswith(("оплата ", "личные средства")) or low == "назначение платежа":
            return index
    return None


def extract_payer_inn(lines: list[str], text: str | None = None) -> str:
    """Извлекает ИНН плательщика из блока плательщика.

    Самый надежный признак в этих PDF — первый ИНН перед блоком
    'Плательщик' или 'Банк плательщика'. Функция никогда не переходит к
    произвольным числам, поэтому не использует даты, суммы, БИК, счета
    или номера счетов на оплату как ИНН.
    """
    payer_index = _find_first_line_index(lines, ("плательщик",))
    if payer_index is not None:
        candidates = inn_values_in_range(lines, max(0, payer_index - 12), payer_index + 1)
        if candidates:
            return candidates[-1]

    bank_payer_index = _find_first_line_index(lines, ("банк плательщика", "банк плательщика"))
    scan_to = bank_payer_index if bank_payer_index is not None else min(len(lines), 20)
    candidates = inn_values_in_range(lines, 0, scan_to)
    if candidates:
        return candidates[0]

    raise ExtractError("не найден ИНН плательщика")


def extract_recipient_inn(lines: list[str], text: str | None = None) -> str:
    """Извлекает ИНН получателя из блока получателя.

    ИНН получателя ищется рядом с метками 'Банк получателя' / точной меткой
    'Получатель' и до текста назначения платежа, поэтому ИНН из банковских
    отметок и посторонние числа не используются как резервный вариант.
    """
    # Сначала проверяем блок банка получателя. Это самое стабильное место как в
    # PDF в стиле Сбера, так и в PDF в стиле Т-Банка.
    bank_recipient_index = _find_first_line_index(lines, ("банк получателя",))
    if bank_recipient_index is not None:
        stop = _first_purpose_index(lines)
        if stop is None or stop <= bank_recipient_index:
            stop = min(len(lines), bank_recipient_index + 25)
        candidates = inn_values_in_range(lines, bank_recipient_index, stop)
        if candidates:
            return candidates[-1]

    # Резервный вариант: точная метка поля 'Получатель'. Не совпадает с 'Банк получателя'.
    recipient_label_index = None
    for index, line in enumerate(lines):
        if line.lower() == "получатель":
            recipient_label_index = index
            break

    if recipient_label_index is not None:
        candidates = inn_values_in_range(lines, max(0, recipient_label_index - 20), recipient_label_index + 1)
        if candidates:
            return candidates[-1]

    purpose_index = _first_purpose_index(lines)
    if purpose_index is not None:
        candidates = inn_values_in_range(lines, 0, purpose_index)
        if len(candidates) >= 2:
            return candidates[-1]

    raise ExtractError("не найден ИНН получателя")

def is_money(value: str) -> bool:
    return bool(MONEY_RE.fullmatch(value.strip()))


def money_values(line: str) -> list[str]:
    return MONEY_RE.findall(line)


def extract_amount(lines: list[str], text: str) -> str:
    """
    Извлекает сумму из поля платежа, а не из текста назначения.
    Это важно для документов, где в назначении указана другая сумма.
    """
    # В большинстве PDF Сбера в этом сценарии встречается такая структура:
    # строка 'Плательщик', затем сумма в нескольких следующих строках.
    for i, line in enumerate(lines):
        if line.lower() == "плательщик":
            for candidate in lines[i + 1 : i + 8]:
                if is_money(candidate):
                    return candidate
                vals = money_values(candidate)
                if vals:
                    return vals[0]

    # В некоторых PDF Т-Банка подпись поля 'Сумма' находится рядом с числовым значением.
    for i, line in enumerate(lines):
        if line.lower() == "сумма":
            for candidate in lines[i : i + 8]:
                if is_money(candidate):
                    return candidate
                vals = money_values(candidate)
                if vals:
                    return vals[0]

    # Более безопасный резервный вариант: все значения, похожие на суммы, до раздела назначения.
    before_purpose = re.split(r"Назначение|Оплата\s+по|Личные\s+средства", text, maxsplit=1, flags=re.IGNORECASE)[0]
    vals = MONEY_RE.findall(before_purpose)
    if vals:
        return vals[-1]

    # Последний резервный вариант: берем первое денежное значение в документе.
    vals = MONEY_RE.findall(text)
    if vals:
        return vals[0]

    raise ExtractError("не найдена сумма платежа")


def is_bank_name(line: str) -> bool:
    low = line.lower()
    return any(
        token in low
        for token in (
            "банк",
            "сбербанк",
            "тбанк",
            "точка",
            "альфа",
            "втб",
            "финсервис",
            "псб",
            "модульбанк",
        )
    )


def looks_like_recipient(line: str) -> bool:
    original = line.strip()
    line = re.sub(r"\s+", " ", original)
    low = line.lower()

    bad_exact = {
        "получатель",
        "плательщик",
        "банк получателя",
        "банк плательщика",
        "назначение платежа",
        "подписи",
        "отметки банка",
        "сч. №",
        "вид оп.",
        "вид платежа",
        "поступ. в банк плат.",
        "списано со сч. плат.",
        "дата",
        "назначение",
        "наз. пл.",
        "платежное поручение",
        "код",
        "кпп",
        "инн",
        "сумма",
        "срок плат.",
        "очер. плат.",
        "рез. поле",
    }

    if low in bad_exact:
        return False
    if low.startswith("оплата ") or low.startswith("личные средства"):
        return False
    if "платежное поручение" in low:
        return False
    if is_bank_name(line):
        return False
    if re.fullmatch(r"\d+[-.,]\d{2}", line):
        return False
    if re.fullmatch(r"\d{8,25}", line):
        return False
    if re.search(r"\b(БИК|К/С|Сч\.?\s*№)\b", line, flags=re.IGNORECASE):
        return False

    upper = line.upper()
    legal_tokens = (
        "ООО",
        "ИП ",
        "АО ",
        "ПАО ",
        "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ",
        "ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ",
        "АКЦИОНЕРНОЕ ОБЩЕСТВО",
    )
    if any(token in upper for token in legal_tokens):
        return True

    words = re.findall(r"[А-ЯЁа-яё]+", line)
    # ФИО / получатель-самозанятый.
    return len(words) >= 2 and not any(word.lower() in {"г", "москва", "дата"} for word in words)


def is_recipient_block_boundary(line: str) -> bool:
    """Проверяет, началось ли после имени следующее поле платежного поручения."""
    normalized = re.sub(r"\s+", " ", line).strip()
    low = normalized.lower()

    if low in {
        "получатель",
        "плательщик",
        "назначение платежа",
        "инн",
        "кпп",
        "бик",
        "сч. №",
        "вид оп.",
        "наз. пл.",
        "срок плат.",
        "очер. плат.",
        "код",
        "рез. поле",
    }:
        return True

    if re.match(r"^(?:ИНН|КПП|БИК|Сч\.?\s*№|Вид\s+оп\.|Наз\.\s*пл\.|Код\b|Рез\.\s*поле)", normalized, re.IGNORECASE):
        return True

    if low.startswith(
        (
            "оплата ",
            "личные средства",
            "зачисление ",
            "перечисление ",
            "возврат ",
            "комиссия ",
            "взносы ",
        )
    ):
        return True

    return bool(re.fullmatch(r"\d{8,25}", normalized))


def recipient_name_from_range(
    lines: list[str],
    start: int,
    end: int,
    *,
    trusted_recipient_block: bool = False,
) -> str | None:
    """Собирает имя получателя из нескольких соседних строк одного блока."""
    parts: list[str] = []
    safe_start = max(0, start)
    safe_end = min(len(lines), end)

    for candidate in lines[safe_start:safe_end]:
        if is_recipient_block_boundary(candidate):
            if parts:
                break
            continue

        words = re.findall(r"[А-ЯЁа-яё]+", candidate)
        is_text_in_trusted_block = (
            trusted_recipient_block
            and len(words) >= 2
            and not is_bank_name(candidate)
        )

        if looks_like_recipient(candidate) or is_text_in_trusted_block:
            parts.append(candidate.strip())
            # Названия длиннее четырех строк в платежной форме практически не
            # встречаются; ограничение не дает захватить следующий текстовый блок.
            if len(parts) >= 4:
                break
        elif parts:
            break

    return " ".join(parts) if parts else None


def extract_payer(lines: list[str]) -> str:
    for i, line in enumerate(lines):
        if line.lower() == "плательщик":
            # Во многих PDF имя плательщика находится выше этой подписи, но не всегда.
            # Это поле необязательное и используется только если пользователь добавил {payer} в паттерн.
            for candidate in reversed(lines[max(0, i - 8) : i]):
                if looks_like_recipient(candidate):
                    return candidate
    return ""


def extract_recipient(lines: list[str], text: str) -> str:
    # Частая структура распознанного текста:
    # Получатель
    # ООО "..."
    # Оплата ...
    for i, line in enumerate(lines):
        if line.lower() == "получатель":
            recipient = recipient_name_from_range(
                lines,
                i + 1,
                i + 9,
                trusted_recipient_block=True,
            )
            if recipient:
                return recipient

    # Частая построчная структура в некоторых банках:
    # Метка банка получателя, строка ИНН, затем фактический получатель перед меткой поля 'Получатель'.
    for i, line in enumerate(lines):
        if "банк получателя" in line.lower():
            recipient = recipient_name_from_range(lines, i + 1, i + 20)
            if recipient:
                return recipient

    # Используем блок ИНН получателя: после строки ИНН/КПП часто идет название получателя.
    for i, line in enumerate(lines):
        if re.search(r"\bИНН\s+\d{10,12}\b", line, flags=re.IGNORECASE):
            recipient = recipient_name_from_range(lines, i + 1, i + 6)
            if recipient:
                return recipient

    # Последний резервный вариант: берем кандидата прямо перед текстом назначения, если он есть.
    purpose_index = None
    for i, line in enumerate(lines):
        if line.lower().startswith(("оплата ", "личные средства")):
            purpose_index = i
            break
    if purpose_index is not None:
        for candidate in reversed(lines[max(0, purpose_index - 8) : purpose_index]):
            if looks_like_recipient(candidate):
                return candidate

    raise ExtractError("не найден получатель")


def clean_recipient_name(name: str, *, title_case_person_names: bool = True) -> str:
    name = re.sub(r"\s+", " ", name.strip())

    name = re.sub(
        r"\bОБЩЕСТВО\s+С\s+ОГРАНИЧЕННОЙ\s+ОТВЕТСТВЕННОСТЬЮ\b",
        "ООО",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(
        r"\bИНДИВИДУАЛЬНЫЙ\s+ПРЕДПРИНИМАТЕЛЬ\b",
        "ИП",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(
        r"\bПУБЛИЧНОЕ\s+АКЦИОНЕРНОЕ\s+ОБЩЕСТВО\b",
        "ПАО",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(
        r"\bАКЦИОНЕРНОЕ\s+ОБЩЕСТВО\b",
        "АО",
        name,
        flags=re.IGNORECASE,
    )

    name = name.replace('"', "").replace("«", "").replace("»", "")
    name = re.sub(r"\bооо\b", "ООО", name, flags=re.IGNORECASE)
    name = re.sub(r"\bип\b", "ИП", name, flags=re.IGNORECASE)
    name = re.sub(r"\bпао\b", "ПАО", name, flags=re.IGNORECASE)
    name = re.sub(r"\bао\b", "АО", name, flags=re.IGNORECASE)

    if title_case_person_names:
        if name.upper() == name:
            if name.startswith("ИП "):
                name = "ИП " + name[3:].title()
            elif not name.startswith(("ООО ", "АО ", "ПАО ")):
                name = name.title()

    name = re.sub(r"\s+", " ", name).strip()
    return name


def format_amount(amount: str, *, spaces: bool = True) -> str:
    amount = amount.strip().replace(",", ".").replace("-", ".")
    if "." not in amount:
        raise ExtractError(f"некорректный формат суммы: {amount}")

    rub, kop = amount.split(".", 1)
    rub_int = int(re.sub(r"\D", "", rub))
    rub_formatted = f"{rub_int:,}".replace(",", " ") if spaces else str(rub_int)
    return f"{rub_formatted}-{kop[:2]}"


def extract_payment_data(pdf_path: Path) -> PaymentData:
    text = extract_text_from_pdf(pdf_path)
    lines = normalize_lines(text)

    return PaymentData(
        number=extract_payment_number(text),
        recipient=extract_recipient(lines, text),
        amount=extract_amount(lines, text),
        date=extract_payment_date(text),
        payer=extract_payer(lines),
        recipient_inn=extract_recipient_inn(lines, text),
        payer_inn=extract_payer_inn(lines, text),
    )
