"""Small, inspectable lexicon sentiment scorer."""

from __future__ import annotations

import re
from dataclasses import dataclass

POSITIVE = frozenset(
    "agree awesome beautiful beneficial clear clean clever correct effective elegant excellent fantastic fast fixed good great helpful impressive improve improved interesting love nice perfect pleased pragmatic promising robust solid thanks thank wonderful".split()
)
NEGATIVE = frozenset(
    "annoying bad blocked broken confusing concern costly cumbersome dangerous difficult disappointed error failure flawed frustrating harmful hate issue missing poor problematic regression risky slow ugly unclear unhappy wrong".split()
)
NEGATORS = frozenset("barely cannot can't didnt didn't don't doesnt doesn't hardly never no not wasn't wont won't".split())
TOKENS = re.compile(r"[a-z]+(?:'[a-z]+)?")


@dataclass(frozen=True)
class Sentiment:
    score: int
    positive_terms: tuple[str, ...]
    negative_terms: tuple[str, ...]

    @property
    def label(self) -> str:
        if self.score > 0:
            return "positive"
        if self.score < 0:
            return "negative"
        return "neutral"


def score_text(text: str) -> Sentiment:
    score = 0
    positive: list[str] = []
    negative: list[str] = []
    negate_next = False
    for token in TOKENS.findall(text.lower()):
        if token in NEGATORS:
            negate_next = True
            continue
        polarity = 1 if token in POSITIVE else -1 if token in NEGATIVE else 0
        if polarity:
            if negate_next:
                polarity *= -1
            if polarity > 0:
                positive.append(token)
            else:
                negative.append(token)
            score += polarity
        negate_next = False
    return Sentiment(score, tuple(positive), tuple(negative))
