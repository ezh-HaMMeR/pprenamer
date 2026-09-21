import unittest

from extractor import clean_recipient_name, extract_payment_date, extract_recipient, looks_like_recipient


class PaymentDateExtractionTests(unittest.TestCase):
    def test_uses_document_date_instead_of_export_date(self) -> None:
        text = "\n".join(
            [
                "18.09.2026",
                "СберБизнес. 03.003.01-6965",
                "03.09.2026",
                "Списано со сч. плат.",
                "ПЛАТЕЖНОЕ ПОРУЧЕНИЕ № 921640",
                "03.09.2026",
                "электронно",
                "Дата",
                "Вид платежа",
                "Сумма",
            ]
        )

        self.assertEqual(extract_payment_date(text), "03.09.2026")


class RecipientExtractionTests(unittest.TestCase):
    def test_extracts_recipient_above_label_and_ignores_seal_placeholder(self) -> None:
        lines = [
            "Банк получателя",
            "ИНН 9715462521",
            'ООО "ТОЛСТОЙ"',
            "Вид оп.",
            "01",
            "Наз. пл.",
            "Получатель",
            "Оплата по заказу клиента",
            "Назначение платежа",
            "Подписи",
            "Отметки банка",
            "М.П.",
        ]

        self.assertEqual(extract_recipient(lines, "\n".join(lines)), 'ООО "ТОЛСТОЙ"')
        self.assertFalse(looks_like_recipient("М.П."))

    def test_extracts_recipient_split_across_two_lines(self) -> None:
        lines = [
            "Банк Получателя",
            "Получатель",
            "ОБЩЕСТВО С ОГРАНИЧЕННОЙ",
            'ОТВЕТСТВЕННОСТЬЮ "РУСУ"',
            "ИНН",
            "9729410160",
            "Назначение платежа",
        ]

        self.assertEqual(
            extract_recipient(lines, "\n".join(lines)),
            'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "РУСУ"',
        )

    def test_does_not_append_payment_purpose(self) -> None:
        lines = [
            "Получатель",
            'ООО "РОМАШКА"',
            "Зачисление средств по операциям эквайринга",
            "Назначение платежа",
        ]

        self.assertEqual(extract_recipient(lines, "\n".join(lines)), 'ООО "РОМАШКА"')

    def test_extracts_multiline_public_recipient_with_city_abbreviation(self) -> None:
        lines = [
            "Получатель",
            "УФК по г. Москве (ОСФР по г. Москве и",
            "Московской области, л/с 04734Ф73010)",
            "ИНН",
            "7703363868",
        ]

        self.assertEqual(
            extract_recipient(lines, "\n".join(lines)),
            "УФК по г. Москве (ОСФР по г. Москве и Московской области, л/с 04734Ф73010)",
        )

    def test_shortens_legal_forms(self) -> None:
        cases = {
            'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "РУСУ"': "ООО РУСУ",
            "ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ ИВАНОВ ИВАН": "ИП Иванов Иван",
            'АКЦИОНЕРНОЕ ОБЩЕСТВО "АЛЬФА"': "АО АЛЬФА",
        }

        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(clean_recipient_name(source), expected)


if __name__ == "__main__":
    unittest.main()
