import unittest

import _bootstrap  # noqa: F401

from app.parsers.utils import normalize_activo_date, normalize_money, parse_wise_pt_date


class UtilsTests(unittest.TestCase):
    def test_money(self):
        self.assertEqual(normalize_money("1 234,56"), 1234.56)
        self.assertEqual(normalize_money("-10,50"), -10.5)

    def test_activo_date(self):
        self.assertEqual(normalize_activo_date("3.04", "2026"), "04.03.2026")

    def test_wise_date(self):
        self.assertEqual(parse_wise_pt_date("31 de dezembro de 2025"), "31.12.2025")


if __name__ == "__main__":
    unittest.main()
