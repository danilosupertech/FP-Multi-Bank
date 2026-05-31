import unittest

import _bootstrap  # noqa: F401

from app.parsers.activobank import ActivoBankParser
from app.parsers.registry import registered_parsers
from app.parsers.wise import WiseParser


class ParserDetectionTests(unittest.TestCase):
    def test_activobank_detection(self):
        self.assertTrue(ActivoBankParser().can_parse_text("Banco ActivoBank EXTRATO"))

    def test_wise_detection(self):
        self.assertTrue(WiseParser().can_parse_text("Wise Payments Ltd. Extrato em EUR Transação: CARD-1"))

    def test_registered_parsers(self):
        bank_names = {parser.bank_name for parser in registered_parsers()}
        self.assertIn("ActivoBank", bank_names)
        self.assertIn("Wise", bank_names)


if __name__ == "__main__":
    unittest.main()
