"""Chinese publisher text rendered in English, because the screen is English.

Two different jobs, and it matters that they stay different:

  * a company NAME is transliterated - there is nothing to translate, only to write in Latin script;
  * a HEADLINE is not translated at all. It is *re-rendered* from the parts the engine already parsed
    (who, which product, what action, how much capacity). Machine-translating a headline would put
    words in a publisher's mouth; composing a sentence from fields we extracted asserts only what the
    classifier already decided, and the publisher's original stays on the record in `source_headline`.

Transliteration order, best first:
  1. the curated map in `export.py` - a real English trading name (万华化学 -> Wanhua Chemical);
  2. a known industry suffix rendered in English (广西氯碱 -> Guangxi Chlor-Alkali);
  3. pinyin, paired two syllables at a time, which is how Chinese company names are actually built
     (山东信发 -> Shandong Xinfa);
  4. if pinyin is unavailable, the name is dropped rather than shown in Chinese.
"""
from __future__ import annotations

import re

_CJK_RE = re.compile(r"[一-鿿]")

# Industry words that carry a settled English equivalent. Longest first so 新材料 wins over 新材.
_SUFFIX_EN: tuple[tuple[str, str], ...] = (
    ("股份有限公司", ""), ("有限公司", ""), ("新材料", "New Materials"), ("新材", "New Materials"),
    ("石油化工", "Petrochemical"), ("石化", "Petrochemical"), ("化工", "Chemical"), ("化学", "Chemical"),
    ("氯碱", "Chlor-Alkali"), ("集团", "Group"), ("股份", ""), ("实业", "Industrial"),
    ("能源", "Energy"), ("科技", "Technology"), ("化纤", "Fibre"), ("大化", "Chemical"),
)

_PRODUCT_EN: dict[str, str] = {
    "TDI": "TDI", "PMDI": "polymeric MDI", "PURE_MDI": "pure MDI", "POLYOL": "polyether polyol",
    "PO": "propylene oxide", "DMF": "DMF", "ADIPIC_ACID": "adipic acid", "NPG": "neopentyl glycol",
    "BDO": "BDO", "TMP": "TMP", "EO": "ethylene oxide", "LIQUID_CHLORINE": "liquid chlorine",
}

# 万吨/年 = ten-thousand tonnes per year. Capacity is a fact in the headline, so it is carried over.
_CAPACITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*万吨\s*/\s*年")


def has_chinese(text: str) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


def _pinyin_pairs(text: str) -> str:
    """Pinyin, capitalised and grouped two syllables at a time - how the names are actually built."""
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        return ""
    syllables = [s for s in lazy_pinyin(text) if s.strip()]
    if not syllables or any(has_chinese(s) for s in syllables):
        return ""
    words = ["".join(syllables[i:i + 2]).capitalize() for i in range(0, len(syllables), 2)]
    return " ".join(words)


def romanise(name: str) -> str:
    """A Latin-script rendering of a Chinese company name, or "" if none can be produced."""
    name = str(name or "").strip()
    if not name:
        return ""
    if not has_chinese(name):
        return name
    suffix_en = ""
    stem = name
    for token, english in _SUFFIX_EN:
        if stem.endswith(token):
            stem = stem[: -len(token)]
            suffix_en = english
            break
    stem_en = _pinyin_pairs(stem) if stem else ""
    if not stem_en:
        # Without a stem there is no name worth showing, and Chinese must not reach the screen.
        return ""
    return f"{stem_en} {suffix_en}".strip()


def product_phrase(material: str | None) -> str:
    key = str(material or "").strip().upper()
    return _PRODUCT_EN.get(key, key.replace("_", " ").lower() if key else "")


def capacity_phrase(headline: str) -> str:
    """"33万吨/年" -> "330,000 t/y". A number the publisher stated, carried over, not invented."""
    match = _CAPACITY_RE.search(str(headline or ""))
    if not match:
        return ""
    tonnes = float(match.group(1)) * 10_000
    return f"{tonnes:,.0f} t/y"


# category -> (sentence for a named actor, sentence for an unnamed one). {actor}/{product} filled in.
_SENTENCES: dict[str, tuple[str, str]] = {
    "FORCE_MAJEURE": ("{actor} declared force majeure on {product}", "Force majeure declared on {product}"),
    "INCIDENT": ("{actor} reported an incident at its {product} plant",
                 "Incident reported at {article} {product} plant"),
    "SHUTDOWN": ("{actor} shut down its {product} plant", "{Article} {product} plant was shut down"),
    "PLANNED_MAINTENANCE": ("{actor} took its {product} plant down for maintenance",
                            "{Article} {product} plant went down for maintenance"),
    "PLANT_STARTUP": ("{actor} started up its {product} plant", "{Article} {product} plant started up"),
    "PRICE_INCREASE": ("{actor} raised its {product} price", "{product} producer prices were raised"),
    "PRICE_DECREASE": ("{actor} lowered its {product} price", "{product} producer prices were lowered"),
    "PRICE_ACTION": ("{actor} issued {article} {product} price update",
                     "{Article} {product} producer price update was issued"),
    "CAPACITY_ADDITION": ("{actor} is adding {product} capacity", "New {product} capacity was reported"),
    "CAPACITY_INVESTMENT": ("{actor} is investing in {product} capacity",
                            "Investment in {product} capacity was reported"),
    # Deliberately flat. The headline says what the venture is; the sentence says only that one was
    # reported, because naming the counterparty or the money would be asserting more than was parsed.
    "CORPORATE_VENTURE": ("{actor} entered {article} {product} venture",
                          "{Article} {product} venture was reported"),
    "CORPORATE_EXIT": ("{actor} is withdrawing from {product}", "A withdrawal from {product} was reported"),
}

# When the headline names no product, "chemical" stands in - which reads fine for a plant ("its
# chemical plant") and badly for a corporate move ("is withdrawing from chemical"). These categories
# get a sentence that simply leaves the product out rather than filling it with a placeholder.
_SENTENCES_NO_PRODUCT: dict[str, tuple[str, str]] = {
    "CORPORATE_VENTURE": ("{actor} entered a venture", "A venture was reported"),
    "CORPORATE_EXIT": ("{actor} is withdrawing from a business", "A withdrawal was reported"),
    "CAPACITY_INVESTMENT": ("{actor} is investing in capacity", "An investment in capacity was reported"),
    "CAPACITY_ADDITION": ("{actor} is adding capacity", "New capacity was reported"),
}


def _article_for(phrase: str) -> str:
    return "an" if phrase[:1].lower() in "aeiou" else "a"


def render_what_changed(*, category: str, label: str, actor: str, named: bool,
                        material: str | None, headline: str) -> str:
    """One English sentence built from what was parsed. Nothing here is translated or inferred."""
    product = product_phrase(material)
    key = str(category or "").upper()
    templates = _SENTENCES.get(key)
    if templates is None:
        return label
    if not product and key in _SENTENCES_NO_PRODUCT:
        templates = _SENTENCES_NO_PRODUCT[key]
    named_template, anonymous_template = templates
    product = product or "chemical"
    article = _article_for(product)
    fields = {"actor": actor, "product": product, "article": article, "Article": article.capitalize()}
    if named and actor and not has_chinese(actor):
        sentence = named_template.format(**fields)
    else:
        # "Unnamed producer (East China)" keeps its region: where matters, and a bare "a producer"
        # throws away the only attribution the publisher gave.
        region = re.search(r"\(([^)]+)\)", str(actor or ""))
        sentence = anonymous_template.format(**fields)
        if region:
            sentence = f"{sentence} ({region.group(1)})"
    capacity = capacity_phrase(headline)
    if capacity:
        sentence = f"{sentence}, {capacity}"
    return sentence
