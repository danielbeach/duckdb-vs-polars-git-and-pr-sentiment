import unittest

from sentiment import score_text


class ScoreTextTests(unittest.TestCase):
    def test_positive_text(self) -> None:
        result = score_text("This is a clear and helpful improvement. Thanks!")
        self.assertEqual(result.label, "positive")
        self.assertEqual(result.score, 3)

    def test_negative_text(self) -> None:
        result = score_text("This is broken, confusing, and risky.")
        self.assertEqual(result.label, "negative")
        self.assertEqual(result.score, -3)

    def test_negation_reverses_next_sentiment_term(self) -> None:
        result = score_text("This is not bad.")
        self.assertEqual(result.label, "positive")
        self.assertEqual(result.score, 1)

    def test_neutral_text(self) -> None:
        result = score_text("Could you update line 12?")
        self.assertEqual(result.label, "neutral")
        self.assertEqual(result.score, 0)


if __name__ == "__main__":
    unittest.main()
