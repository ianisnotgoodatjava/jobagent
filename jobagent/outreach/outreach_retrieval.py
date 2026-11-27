"""Retrieval over your past outreach emails.

Given a new target (company, team/area, contact title), finds the past
emails most similar to it, so a new draft can be grounded in what you
actually wrote before rather than a generic template.

Why retrieval and not a trained model: with a few hundred examples and no
labels for "did this work", nearest-neighbour retrieval over TF-IDF vectors
is the technique that actually fits the data. A classifier here would be
fitting noise and reporting confident numbers that mean nothing.
"""

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from jobagent.outreach import outreach_corpus


# How fast an old email stops being a good model. At one half-life its
# similarity score counts for half as much, so a close-enough recent email
# beats a slightly better match from a year ago.
#
# Topic similarity alone was ranking February emails ahead of August ones for
# the same desk, and the two it handed the generator were the examples the
# generator was told to match the rhythm of. His rhythm moved in that time:
# five paragraphs and 159 words became six and 188. Showing him his own older
# writing is how the drafts kept coming back sounding like last year.
HALF_LIFE_DAYS = 120

# How much more an email that got a human reply counts, against one that did
# not. Topic decides which emails are candidates; among close candidates the
# one that worked should be the one shown. Two, not ten: with about one reply
# in eight, a large weight would hand every draft the same three emails
# regardless of the desk, and the examples would stop matching the topic.
REPLY_WEIGHT = 2.0


def _age_days(record: dict, newest) -> float:
    """Days between this email and the newest in the corpus.

    Measured against the corpus, not against today, for the same reason the
    style window is: the corpus is rebuilt by hand, and anchoring on today
    would decay every single record the moment it fell behind — eventually
    weighting them all to nearly nothing and leaving the ranking to
    floating-point noise.
    """
    import datetime
    raw = (record.get("date") or "")[:10]
    try:
        when = datetime.date.fromisoformat(raw)
    except ValueError:
        # No usable date: treat as one half-life old. Neither trusted as
        # current nor discarded.
        return float(HALF_LIFE_DAYS)
    return max(0.0, (newest - when).days)


class OutreachIndex:
    def __init__(self, records: list[dict]):
        self.records = records
        # Index the *topic* of each email (subject area, company, subject
        # line) rather than the whole body: the bodies share so much boilerplate
        # ("My name is <name>... I recently graduated from <school>") that
        # full-body vectors make everything look equally similar.
        self.corpus = [
            f"{r.get('area', '')} {r.get('company', '')} {r.get('subject', '')}"
            for r in records
        ]
        self.vectorizer = TfidfVectorizer(
            stop_words="english",
            ngram_range=(1, 2),
            sublinear_tf=True,
            min_df=1,
        )
        self.matrix = self.vectorizer.fit_transform(self.corpus)

        # Precomputed once, so search stays a single multiply.
        import datetime
        dates = []
        for r in records:
            raw = (r.get("date") or "")[:10]
            try:
                dates.append(datetime.date.fromisoformat(raw))
            except ValueError:
                pass
        self.newest = max(dates) if dates else datetime.date.today()
        self.decay = [0.5 ** (_age_days(r, self.newest) / HALF_LIFE_DAYS)
                      for r in records]

    def search(self, query: str, k: int = 3, exclude_follow_ups: bool = True,
               half_life: float | None = HALF_LIFE_DAYS) -> list[tuple[float, dict]]:
        """Nearest past emails by topic, with recent ones preferred.

        The returned score is the decayed one, which is what the ranking used.
        Pass half_life=None for pure topic similarity.
        """
        vec = self.vectorizer.transform([query])
        scores = cosine_similarity(vec, self.matrix)[0]
        if half_life is None:
            weighted = list(scores)
        elif half_life == HALF_LIFE_DAYS:
            weighted = [s * d for s, d in zip(scores, self.decay)]
        else:
            weighted = [s * 0.5 ** (_age_days(r, self.newest) / half_life)
                        for s, r in zip(scores, self.records)]
        weighted = [w * (REPLY_WEIGHT if r.get("replied") else 1.0)
                    for w, r in zip(weighted, self.records)]
        # Sorted on the decayed score, then on date, so a tie resolves to the
        # newer email explicitly rather than by chance. It happened to come
        # out right before, because the corpus file is written newest-first
        # and Python's sort is stable — an accident nobody had written down
        # and that a rebuild in another order would have silently reversed.
        ranked = sorted(zip(weighted, self.records),
                        key=lambda p: (-p[0], _age_days(p[1], self.newest)))
        out = []
        for score, rec in ranked:
            # First-contact outreach only. A thank-you after an interview or
            # an availability reply is not a model for a cold email, and the
            # corpus's own flag only ever caught subjects starting "Re:".
            if exclude_follow_ups and (rec.get("is_follow_up")
                                       or rec.get("kind", "outreach") != "outreach"):
                continue
            if score <= 0:
                continue
            out.append((float(score), rec))
            if len(out) >= k:
                break
        return out


def load_index(path: str = outreach_corpus.CORPUS_FILE) -> OutreachIndex:
    """The index over his emails, one per piece of writing, each marked with
    its kind and whether it got a reply (from logs/outreach_replies.json)."""
    from jobagent.outreach import reply_labels
    return OutreachIndex(reply_labels.annotate(outreach_corpus.load(path)))


def build_query(company: str = "", area: str = "", title: str = "", role: str = "") -> str:
    return " ".join(p for p in (company, area, title, role) if p)
