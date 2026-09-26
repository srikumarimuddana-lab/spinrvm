"""FAQ retrieval quality eval for the AI assistant's search_faqs tool.

Measures whether the lexical ranker in ai/tools_support.py hands the model
the RIGHT help-centre article for realistic questions. The model is told to
answer only from tool results (ai/prompts.py GROUND RULES), so when the
right FAQ is not in the top 5 the user gets "I couldn't find that" — or an
answer built from an unrelated FAQ — for a question the help centre does
answer. That is the failure this file tracks.

Corpus: data/faq_corpus_snapshot.json mirrors the 68 active production
``faqs`` rows as of 2026-09-25 (ids replaced with readable slugs): the original
60, plus 8 payment/payout/age FAQs added and 3 payout/cancellation answers
rewritten that day (docs/change-log/2026-09-25-faq-content-payments-payouts.md).
Keep it in step with production when FAQs change. Production runs with ai_faq_semantic_enabled=false and no stored
embeddings, so lexical ranking IS the live path this measures.

The thresholds are a RATCHET, not a target: they pin the measured baseline so
a ranking change can only move quality up. When a change improves a score,
raise the floor in the same commit. Never lower one to make a change pass.

Offline and deterministic — no DB, no provider calls — so it is CI-safe.
"""

import json
import os

import pytest

from backend.ai import tools_support

pytestmark = pytest.mark.unit

_CORPUS_PATH = os.path.join(os.path.dirname(__file__), "data", "faq_corpus_snapshot.json")
with open(_CORPUS_PATH, encoding="utf-8") as _fh:
    CORPUS = json.load(_fh)
_ID_BY_QUESTION = {row["question"]: row["id"] for row in CORPUS}


def _rows_for(audience):
    # Mirrors _faq_audiences() for a signed-in user: own audience + "both".
    return [r for r in CORPUS if r["audience"] in (audience, "both")]


def _ranked_ids(audience, query):
    results = tools_support._lexical_results(_rows_for(audience), query, None)
    return [_ID_BY_QUESTION[r["question"]] for r in results]


# (audience, what a user actually types, a keyword query a tool-calling model
# would send, acceptable FAQ ids). Natural phrasings are deliberately NOT
# copied from FAQ question text. Keyword queries were written before measuring.
CASES = [
    ("rider", "how much does a ride cost", "fare cost calculated", {"r_fare_calc"}),
    (
        "rider",
        "why did I get charged so much for my last trip",
        "fare higher than expected",
        {"r_fare_higher", "r_refunds"},
    ),
    (
        "rider",
        "can I get my money back? the driver took a long route",
        "refund long route",
        {"r_refunds", "r_fare_higher"},
    ),
    ("rider", "is there a fee if I cancel", "cancellation fee", {"r_cancel"}),
    ("rider", "can I book a ride for tomorrow morning", "schedule ride later", {"r_schedule"}),
    ("rider", "I left my phone in the car", "lost item left phone", {"r_lost_item"}),
    ("rider", "do you take cash", "cash payment methods", {"r_pay_methods", "r_cash"}),
    ("rider", "can I pay with apple pay", "apple pay payment methods", {"r_pay_methods"}),
    ("rider", "how do I add money to my wallet", "wallet top up", {"r_wallet_topup"}),
    ("rider", "where do I enter a coupon", "promo code", {"r_promo"}),
    ("rider", "what is surge", "surge pricing", {"r_surge"}),
    ("rider", "prices are really high right now why", "surge pricing high prices", {"r_surge", "r_fare_higher"}),
    ("rider", "I need a wheelchair van", "wheelchair accessible vehicle", {"r_wav"}),
    ("rider", "can I bring my guide dog", "service animal guide dog", {"r_wav"}),
    ("rider", "do you have car seats for kids", "child car seat", {"r_child_seat"}),
    ("rider", "I want to close my account", "delete account", {"delete_account"}),
    ("rider", "remove all my personal info", "delete personal information", {"delete_account", "data_retention"}),
    ("rider", "how do I download my data", "download my data copy", {"data_copy"}),
    ("rider", "is my data stored in canada", "personal information stored canada", {"data_location"}),
    ("rider", "what happens if we get in an accident during the ride", "accident insurance during ride", {"r_insured"}),
    ("rider", "is there an emergency button", "emergency SOS button", {"safety_features"}),
    ("rider", "how do I get an invoice for my expense report", "trip receipt invoice", {"r_receipt"}),
    ("rider", "my work ride was declined", "work ride blocked company policy", {"r_policy_block"}),
    ("rider", "how do I charge a ride to my company", "corporate account billing", {"r_corporate"}),
    ("rider", "how old do you have to be to use spinr", "minimum age account", {"min_age"}),
    ("rider", "how do I order a car", "book a ride", {"r_book"}),
    ("driver", "when do I get my money", "driver payout timing", {"d_get_paid"}),
    ("driver", "how much commission do you take", "commission fare keep", {"d_keep_fare"}),
    ("driver", "what percentage does spinr take from drivers", "commission percentage", {"d_keep_fare"}),
    ("driver", "the app won't let me go online", "can't go online blocked", {"d_why_offline"}),
    ("driver", "my background check expired", "criminal record check expired", {"d_crc_expired", "d_crc_reqs"}),
    ("driver", "where can I get a police check in regina", "criminal record check regina", {"d_crc_where"}),
    ("driver", "how old can my car be", "vehicle age requirement", {"d_vehicle_reqs", "d_requirements"}),
    (
        "driver",
        "can I drive with a 2012 honda civic",
        "vehicle requirements year",
        {"d_vehicle_reqs", "d_requirements"},
    ),
    (
        "driver",
        "what do I need to become a driver",
        "driver requirements sign up",
        {"d_requirements", "d_signup", "d_docs_needed"},
    ),
    ("driver", "my licence photo got rejected", "document rejected", {"d_doc_rejected"}),
    ("driver", "how long until I'm approved", "application approval time", {"d_review_time", "d_approve_faster"}),
    ("driver", "rider never came out, do I get paid", "rider no-show", {"d_noshow"}),
    ("driver", "do I get a T4 at the end of the year", "T4A tax year end", {"d_taxes"}),
    ("driver", "do I have to charge GST", "GST taxes drivers", {"d_taxes"}),
    (
        "driver",
        "am I covered by insurance while waiting for rides",
        "insurance coverage online waiting",
        {"d_insurance_periods"},
    ),
    ("driver", "can I refuse a passenger with a dog", "refuse service animal", {"d_service_animal"}),
    ("driver", "do I have to work set hours", "independent contractor shifts", {"d_contractor"}),
    ("driver", "I changed cars, how do I update it", "update vehicle details", {"d_update_vehicle"}),
    ("driver", "how many years of driving do I need", "driving experience years", {"d_experience"}),
    ("driver", "do I need a class 4 licence", "driver licence class", {"d_licence"}),
    ("driver", "the start trip button isn't working", "can't start ride", {"d_cant_start", "d_cant_start_accepted"}),
    ("driver", "how do I talk to someone at spinr", "contact support", {"d_contact"}),
    # Out-of-corpus until the 2026-09-25 FAQ additions; phrasing predates them.
    ("rider", "can I tip my driver", "tip driver", {"r_tip"}),
    ("driver", "what is the minimum age to drive", "minimum age driver", {"d_min_age"}),
    ("driver", "how do I set up direct deposit", "direct deposit bank account", {"d_bank"}),
]

# Questions the help centre does NOT answer. The ideal tool result is the
# "no match" note, which tells the model to say so and offer escalation.
OUT_OF_CORPUS = [
    ("rider", "do you operate in calgary"),
    ("rider", "can I bring my cat in a carrier"),
    ("rider", "can I smoke in the car"),
    ("rider", "how many passengers can I bring"),
    ("rider", "can I choose a female driver"),
    ("driver", "can I drive in saskatoon and regina"),
]

# Measured 2026-09-25 against the snapshot above. Ratchet: raise, never lower.
# (60-FAQ corpus, 48 cases: 23 / 32 / 43 / 48. After the FAQ additions, 51 cases.)
FLOOR_NATURAL_TOP1 = 26
FLOOR_NATURAL_TOP5 = 36
FLOOR_KEYWORD_TOP1 = 45
FLOOR_KEYWORD_TOP5 = 51


def _score(query_index):
    top1 = top5 = 0
    misses = []
    for case in CASES:
        audience, query, want = case[0], case[query_index], case[3]
        ids = _ranked_ids(audience, query)
        rank = next((i for i, fid in enumerate(ids) if fid in want), None)
        top1 += rank == 0
        top5 += rank is not None
        if rank is None:
            misses.append((audience, query, ids[:3]))
    return top1, top5, misses


def test_corpus_snapshot_is_well_formed():
    assert len(CORPUS) == 68
    assert len(_ID_BY_QUESTION) == len(CORPUS), "duplicate FAQ question text"
    ids = {r["id"] for r in CORPUS}
    for case in CASES:
        assert case[3] <= ids, f"unknown expected id in {case}"


def test_natural_question_retrieval_does_not_regress():
    top1, top5, misses = _score(1)
    report = "\n".join(f"  [{a}] {q!r} -> {got}" for a, q, got in misses)
    assert top1 >= FLOOR_NATURAL_TOP1, f"top-1 {top1}/{len(CASES)} < floor {FLOOR_NATURAL_TOP1}"
    assert top5 >= FLOOR_NATURAL_TOP5, f"top-5 {top5}/{len(CASES)} < floor {FLOOR_NATURAL_TOP5}\n{report}"


def test_keyword_query_retrieval_does_not_regress():
    top1, top5, misses = _score(2)
    report = "\n".join(f"  [{a}] {q!r} -> {got}" for a, q, got in misses)
    assert top1 >= FLOOR_KEYWORD_TOP1, f"top-1 {top1}/{len(CASES)} < floor {FLOOR_KEYWORD_TOP1}"
    assert top5 >= FLOOR_KEYWORD_TOP5, f"top-5 {top5}/{len(CASES)} < floor {FLOOR_KEYWORD_TOP5}\n{report}"
