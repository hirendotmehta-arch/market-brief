"""
Market News Brief for Indian equities
-------------------------------------
Pulls Indian + international business news (RSS) and corporate announcements
filed with BSE / NSE, then asks Google Gemini (free tier) to summarise each
important item and tag which Indian sectors and stocks it helps or hurts.

Runs on Streamlit Community Cloud (free). Secrets needed:
    GEMINI_API_KEY = "..."          (required for analysis)
    APP_PASSWORD   = "..."          (optional, locks the page)
    GEMINI_MODEL   = "..."          (optional, default gemini-flash-latest)
"""

import html
import json
import os
import re
from datetime import datetime, timedelta, timezone

import altair as alt
import feedparser
import pandas as pd
import requests
import streamlit as st

IST = timezone(timedelta(hours=5, minutes=30))
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

st.set_page_config(page_title="Market News Brief", page_icon="📰", layout="wide")


# ------------------------------------------------------------------ settings
def secret(name, default=""):
    try:
        return st.secrets.get(name, os.getenv(name, default))
    except Exception:
        return os.getenv(name, default)


GEMINI_KEY = secret("GEMINI_API_KEY")
GEMINI_MODEL = secret("GEMINI_MODEL", "gemini-flash-latest")
APP_PASSWORD = secret("APP_PASSWORD")

# Edit these lists freely. A feed that fails is skipped and shown under "Sources".
INDIA_FEEDS = {
    "Economic Times Markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Economic Times Economy": "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
    "Business Standard Markets": "https://www.business-standard.com/rss/markets-106.rss",
    "Business Standard Companies": "https://www.business-standard.com/rss/companies-101.rss",
    "Mint Markets": "https://www.livemint.com/rss/markets",
    "Mint Companies": "https://www.livemint.com/rss/companies",
    "Moneycontrol Business": "https://www.moneycontrol.com/rss/business.xml",
    "BusinessLine Markets": "https://www.thehindubusinessline.com/markets/feeder/default.rss",
    "Google News India markets": "https://news.google.com/rss/search?q=sensex+OR+nifty+OR+rbi+OR+sebi+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
}
WORLD_FEEDS = {
    "CNBC World Markets": "https://www.cnbc.com/id/100727362/device/rss/rss.html",
    "CNBC Economy": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "BBC Business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "Google News global markets": "https://news.google.com/rss/search?q=(fed+OR+crude+OR+dollar+OR+china+OR+tariff)+markets+when:1d&hl=en-US&gl=US&ceid=US:en",
}

SECTORS = [
    "Banks", "NBFC & Financials", "Insurance", "IT", "Pharma & Healthcare",
    "Auto & Auto Ancillaries", "FMCG", "Metals & Mining", "Oil & Gas Upstream",
    "Oil Marketing (OMCs)", "Power & Utilities", "Renewable Energy",
    "Capital Goods & Infra", "Defence", "Cement", "Real Estate", "Chemicals",
    "Paints", "Consumer Durables", "Telecom", "Media", "Textiles", "Aviation",
    "Logistics", "Agri & Fertilisers", "Retail", "Hotels & Travel",
]

# Exchange filings: drop routine paperwork, keep anything price-relevant.
SKIP_WORDS = [
    "trading window", "compliance certificate", "newspaper publication", "loss of share",
    "duplicate share", "74(5)", "regulation 74", "investor complaint", "shareholding pattern",
    "change in address", "postal ballot", "record date", "book closure", "esop", "esos",
    "structural digital database", "secretarial compliance", "transmission of share",
    "transcript", "audio recording", "analysts/institutional investor meet", "con. call",
    "conference call", "earnings call", "intimation of schedule", "certificate under",
    "closure of trading", "registrar", "kmp under regulation 30 - clarif",
]
KEEP_WORDS = [
    "order", "contract", "acqui", "merger", "amalgamat", "demerger", "financial result",
    "results", "dividend", "buyback", "buy back", "bonus", "split", "qip", "preferential",
    "rights issue", "fund rais", "ncd", "credit rating", "rating", "resign", "ceo", "cfo",
    "managing director", "fire", "shutdown", "plant", "capacity", "expansion",
    "commission", "usfda", "warning letter", "import alert", "approval", "launch",
    "joint venture", "partnership", "mou", "agreement", "stake", "pledge", "insolvency",
    "nclt", "default", "penalty", "tax demand", "show cause", "litigation", "arbitration",
    "award", "guidance", "outcome of board", "production", "business update", "sales",
    "strike", "lockout", "fraud", "search", "raid", "investment", "subsidiary",
]


# ------------------------------------------------------------------ helpers
def clean(text, n=300):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:n]


def parse_time(value):
    if not value:
        return None
    try:
        t = pd.to_datetime(value, dayfirst=not re.match(r"\d{4}-", str(value)), errors="coerce")
    except Exception:
        return None
    if pd.isna(t):
        return None
    t = t.to_pydatetime()
    return t.replace(tzinfo=IST) if t.tzinfo is None else t.astimezone(IST)


def entry_time(e):
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc).astimezone(IST)
    return None


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower())[:70]


def is_important_filing(text):
    t = text.lower()
    if any(w in t for w in SKIP_WORDS):
        return False
    if "board meeting" in t and "outcome" not in t and not any(
        w in t for w in ("fund", "bonus", "split", "buyback", "buy back", "dividend")
    ):
        return False
    return any(w in t for w in KEEP_WORDS)


# ------------------------------------------------------------------ fetchers
def fetch_feeds(feeds, region, cutoff):
    items, status = [], {}
    for name, url in feeds.items():
        try:
            r = requests.get(url, headers=UA, timeout=15)
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
            n = 0
            for e in parsed.entries[:40]:
                t = entry_time(e)
                if t and t < cutoff:
                    continue
                items.append({
                    "kind": "news", "region": region, "source": name,
                    "title": clean(e.get("title"), 200),
                    "text": clean(e.get("summary"), 300),
                    "url": e.get("link", ""), "time": t,
                })
                n += 1
            status[name] = f"{n} items"
        except Exception as ex:
            status[name] = f"failed ({type(ex).__name__})"
    return items, status


def fetch_bse(cutoff, pages=8):
    today = datetime.now(IST)
    hdr = {**UA, "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com",
           "Accept": "application/json, text/plain, */*"}
    out = []
    for p in range(1, pages + 1):
        params = {
            "pageno": p, "strCat": "-1", "strPrevDate": (today - timedelta(days=1)).strftime("%Y%m%d"),
            "strScrip": "", "strSearch": "P", "strToDate": today.strftime("%Y%m%d"),
            "strType": "C", "subcategory": "-1",
        }
        r = requests.get("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w",
                         params=params, headers=hdr, timeout=20)
        r.raise_for_status()
        rows = r.json().get("Table", [])
        if not rows:
            break
        for x in rows:
            t = parse_time(x.get("NEWS_DT") or x.get("DissemDT"))
            if t and t < cutoff:
                continue
            subject = (x.get("NEWSSUB") or "").split(" - ")[-1]
            att = x.get("ATTACHMENTNAME") or ""
            out.append({
                "kind": "filing", "source": "BSE", "company": clean(x.get("SLONGNAME"), 80),
                "category": x.get("CATEGORYNAME") or "",
                "title": clean(subject, 200), "text": clean(x.get("HEADLINE"), 400),
                "url": f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}" if att
                else "https://www.bseindia.com/corporates/ann.html",
                "time": t,
            })
    return out


def fetch_nse(cutoff):
    s = requests.Session()
    s.headers.update({**UA, "Accept": "application/json",
                      "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"})
    s.get("https://www.nseindia.com", timeout=15)
    r = s.get("https://www.nseindia.com/api/corporate-announcements?index=equities", timeout=20)
    r.raise_for_status()
    data = r.json()
    rows = data if isinstance(data, list) else data.get("data", [])
    out = []
    for x in rows:
        t = parse_time(x.get("an_dt") or x.get("sort_date"))
        if t and t < cutoff:
            continue
        out.append({
            "kind": "filing", "source": "NSE",
            "company": f"{clean(x.get('sm_name'), 70)} ({x.get('symbol', '')})",
            "category": x.get("desc") or "",
            "title": clean(x.get("desc"), 200), "text": clean(x.get("attchmntText"), 400),
            "url": x.get("attchmntFile") or "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            "time": t,
        })
    return out


# ------------------------------------------------------------------ AI
def build_prompt(items, max_items):
    lines = []
    for it in items:
        when = it["time"].strftime("%d-%b %H:%M") if it["time"] else "time n/a"
        if it["kind"] == "news":
            lines.append(f"[{it['ref']}] {when} | {it['region']} news | {it['source']} | "
                         f"{it['title']} | {it['text']}")
        else:
            lines.append(f"[{it['ref']}] {when} | {it['source']} filing | {it['company']} | "
                         f"{it['category']} | {it['title']} | {it['text']}")
    schema = {
        "market_mood": "2-3 sentences: overall picture for an Indian equity investor right now",
        "top_points": ["5-8 one-line key takeaways, most important first"],
        "items": [{
            "ref": "N3", "also": ["N7"], "headline": "short clear headline",
            "summary": "1-2 sentences of what happened",
            "impact": "positive | negative | mixed | neutral", "importance": 4,
            "sectors_up": ["Banks"], "sectors_down": ["IT"],
            "stocks_up": ["HDFCBANK"], "stocks_down": ["INFY"],
            "why": "one or two sentences on how this moves those stocks",
        }],
    }
    return f"""You are an equity research desk analyst covering Indian markets (NSE/BSE).
Current time: {datetime.now(IST).strftime('%d %b %Y %H:%M')} IST.
Below are recent business news items (refs N...) and corporate announcements filed with
Indian exchanges (refs F...).

Task
1. Keep only items that matter for Indian listed stocks. Ignore routine, trivial or duplicate
   items. When several items describe the same event, keep one ref and list the others in "also".
2. For each kept item judge the likely short-term (days to weeks) price impact on Indian
   listed companies.
   - For news, think through second-order effects (e.g. falling crude helps OMCs, paints,
     aviation, tyres and hurts upstream producers). For international news, explain the
     route to India: FII flows, rupee, commodity prices, export demand, tariffs, rates.
   - For a filing, the filing company is the main stock affected; add peers only if clearly relevant.
   - Use sector names only from this list: {", ".join(SECTORS)}.
   - Stocks: NSE symbols where you are confident (e.g. TATAMOTORS, HDFCBANK), otherwise the
     company name. Never invent companies.
   - importance: 5 market-moving or index-level, 4 big for a sector or a large-cap,
     3 meaningful for one company, 2 minor. Do not include anything below 2.
   - Judge only from the text given. If it is too thin to judge, mark "neutral" and say so in "why".
3. Return at most {max_items} items, most important first.

Reply with ONLY a JSON object in exactly this shape:
{json.dumps(schema, indent=1)}

ITEMS
""" + "\n".join(lines)


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("The AI reply had no JSON in it.")
    return json.loads(text[start:end + 1])


def ask_gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
                             "maxOutputTokens": 32000},
    }
    r = requests.post(url, json=body, timeout=240,
                      headers={"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"})
    if r.status_code == 429:
        raise RuntimeError("Gemini free-tier limit reached. Wait a minute, then press Refresh again.")
    if r.status_code == 404:
        raise RuntimeError(f"Model '{GEMINI_MODEL}' not found. Set GEMINI_MODEL in Secrets to a current "
                           "Gemini Flash model name from aistudio.google.com.")
    if r.status_code in (400, 403):
        raise RuntimeError(f"Gemini refused the request ({r.status_code}). Check GEMINI_API_KEY. "
                           f"Detail: {r.text[:200]}")
    r.raise_for_status()
    cand = r.json().get("candidates", [{}])[0]
    parts = cand.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise RuntimeError(f"Gemini returned no text (finish reason: {cand.get('finishReason')}).")
    return parse_json(text)


# ------------------------------------------------------------------ pipeline
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def build_brief(hours, use_nse, max_items):
    cutoff = datetime.now(IST) - timedelta(hours=hours)
    status = {}

    india, s1 = fetch_feeds(INDIA_FEEDS, "India", cutoff)
    world, s2 = fetch_feeds(WORLD_FEEDS, "World", cutoff)
    status.update(s1)
    status.update(s2)

    filings = []
    try:
        b = fetch_bse(cutoff)
        status["BSE announcements"] = f"{len(b)} filings"
        filings += b
    except Exception as ex:
        status["BSE announcements"] = f"failed ({type(ex).__name__})"
    if use_nse:
        try:
            n = fetch_nse(cutoff)
            status["NSE announcements"] = f"{len(n)} filings"
            filings += n
        except Exception as ex:
            status["NSE announcements"] = f"failed ({type(ex).__name__}); NSE often blocks cloud servers"

    # de-duplicate
    seen, news = set(), []
    for it in sorted(india + world, key=lambda x: x["time"] or datetime.min.replace(tzinfo=IST), reverse=True):
        k = norm(it["title"])
        if k and k not in seen:
            seen.add(k)
            news.append(it)
    seen, important = set(), []
    total_filings = len(filings)
    for it in sorted(filings, key=lambda x: x["time"] or datetime.min.replace(tzinfo=IST), reverse=True):
        blob = f"{it['category']} {it['title']} {it['text']}"
        k = norm(it["company"].split("(")[0][:20] + it["text"][:50])
        if is_important_filing(blob) and k not in seen:
            seen.add(k)
            important.append(it)

    india_n = [x for x in news if x["region"] == "India"][:70]
    world_n = [x for x in news if x["region"] == "World"][:35]
    important = important[:90]
    for i, it in enumerate(india_n + world_n, 1):
        it["ref"] = f"N{i}"
    for i, it in enumerate(important, 1):
        it["ref"] = f"F{i}"
    pool = india_n + world_n + important

    result = {
        "generated_at": datetime.now(IST), "status": status, "pool": pool,
        "counts": {"india": len(india_n), "world": len(world_n),
                   "filings_total": total_filings, "filings_kept": len(important)},
        "ai": None, "ai_error": None,
    }
    if not GEMINI_KEY:
        result["ai_error"] = "Add GEMINI_API_KEY in the app's Secrets to get the impact analysis."
    elif pool:
        try:
            result["ai"] = ask_gemini(build_prompt(pool, max_items))
        except Exception as ex:
            result["ai_error"] = str(ex)
    return result


# ------------------------------------------------------------------ UI
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&display=swap');
html, body, [class*="css"], .stMarkdown { font-family: 'Manrope', system-ui, sans-serif; }
h1.brand { font-weight: 800; letter-spacing: -0.02em; margin: 0; font-size: 1.9rem; }
.stamp { color: #5B6472; font-size: .85rem; margin-top: .15rem; }
.mood { background: #1B2A41; color: #F3F6F9; padding: 16px 18px; border-radius: 8px;
        font-size: 1.02rem; line-height: 1.6; margin: .6rem 0 1rem; }
.card { background: #FFFFFF; border: 1px solid #DCE2E9; border-left: 5px solid #5B6472;
        border-radius: 6px; padding: 12px 14px; margin: 0 0 10px; }
.card.positive { border-left-color: #1E7F4F; }
.card.negative { border-left-color: #B3261E; }
.card.mixed    { border-left-color: #B7791F; }
.meta { font-size: .78rem; color: #5B6472; display: flex; gap: 12px; flex-wrap: wrap; }
.badge { font-weight: 800; }
.badge.positive { color: #1E7F4F; } .badge.negative { color: #B3261E; }
.badge.mixed { color: #B7791F; } .badge.neutral { color: #5B6472; }
a.hl { display: block; font-weight: 700; font-size: 1.02rem; color: #1B2A41;
       text-decoration: none; margin: .3rem 0 .2rem; line-height: 1.35; }
a.hl:hover { text-decoration: underline; }
.card p { margin: .2rem 0; font-size: .92rem; color: #1B2A41; line-height: 1.5; }
.tag { display: inline-block; padding: 2px 9px; border-radius: 12px; font-size: .78rem;
       margin: 3px 5px 0 0; }
.tag.up { background: #E1F1E8; color: #145C38; }
.tag.down { background: #F7E2E0; color: #8A1C16; }
.tag.stock { font-weight: 700; }
.card p.why { color: #3A4A63; font-size: .86rem; margin-top: .45rem; }
</style>
"""


def esc(s):
    return html.escape(str(s or ""))


def card(it, src):
    imp = (it.get("impact") or "neutral").lower()
    imp = imp if imp in ("positive", "negative", "mixed", "neutral") else "neutral"
    meta = [f'<span class="badge {imp}">{imp.capitalize()}</span>',
            f'<span>Importance {esc(it.get("importance", "?"))}/5</span>']
    if src:
        label = src["region"] + " news" if src["kind"] == "news" else src["source"] + " filing"
        meta.append(f"<span>{esc(label)}</span>")
        meta.append(f"<span>{esc(src['company'] if src['kind'] == 'filing' else src['source'])}</span>")
        if src.get("time"):
            meta.append(f"<span>{src['time'].strftime('%d %b, %H:%M')}</span>")
    url = esc(src["url"]) if src else "#"
    tags = "".join(f'<span class="tag up">▲ {esc(s)}</span>' for s in it.get("sectors_up", []))
    tags += "".join(f'<span class="tag down">▼ {esc(s)}</span>' for s in it.get("sectors_down", []))
    tags += "".join(f'<span class="tag up stock">▲ {esc(s)}</span>' for s in it.get("stocks_up", []))
    tags += "".join(f'<span class="tag down stock">▼ {esc(s)}</span>' for s in it.get("stocks_down", []))
    return f"""<div class="card {imp}">
<div class="meta">{''.join(meta)}</div>
<a class="hl" href="{url}" target="_blank" rel="noopener">{esc(it.get('headline'))}</a>
<p>{esc(it.get('summary'))}</p>
<div>{tags}</div>
<p class="why">{esc(it.get('why'))}</p>
</div>"""


def sector_chart(items):
    rows = {}
    for it in items:
        w = int(it.get("importance") or 1)
        for s in it.get("sectors_up", []):
            r = rows.setdefault(s, {"Sector": s, "Score": 0, "Up": 0, "Down": 0})
            r["Score"] += w
            r["Up"] += 1
        for s in it.get("sectors_down", []):
            r = rows.setdefault(s, {"Sector": s, "Score": 0, "Up": 0, "Down": 0})
            r["Score"] -= w
            r["Down"] += 1
    df = pd.DataFrame([r for r in rows.values() if r["Up"] or r["Down"]])
    if df.empty:
        return None
    return (
        alt.Chart(df).mark_bar(cornerRadius=2).encode(
            x=alt.X("Score:Q", title="Net tilt (importance-weighted)"),
            y=alt.Y("Sector:N", sort="-x", title=None),
            color=alt.condition(alt.datum.Score >= 0, alt.value("#1E7F4F"), alt.value("#B3261E")),
            tooltip=["Sector", "Score", alt.Tooltip("Up", title="Positive items"),
                     alt.Tooltip("Down", title="Negative items")],
        ).properties(height=max(180, 26 * len(df)))
    )


def raw_table(items):
    if not items:
        st.caption("Nothing fetched for this group.")
        return
    df = pd.DataFrame([{
        "Time": it["time"].strftime("%d %b %H:%M") if it.get("time") else "",
        "Source": it["source"] if it["kind"] == "news" else f"{it['source']}: {it['company']}",
        "Title": it["title"], "Link": it["url"],
    } for it in items])
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"Link": st.column_config.LinkColumn("Link", display_text="Open")})


# password gate
if APP_PASSWORD and not st.session_state.get("authed"):
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown('<h1 class="brand">Market news brief</h1>', unsafe_allow_html=True)
    pw = st.text_input("Password", type="password")
    if pw == APP_PASSWORD:
        st.session_state.authed = True
        st.rerun()
    elif pw:
        st.error("That password is wrong.")
    st.stop()

st.markdown(CSS, unsafe_allow_html=True)

top_l, top_r = st.columns([3, 1])
with top_l:
    st.markdown('<h1 class="brand">Market news brief</h1>', unsafe_allow_html=True)
with top_r:
    refresh = st.button("Refresh", type="primary", width="stretch")

with st.expander("Settings"):
    hours = st.select_slider("Look back", options=[3, 6, 12, 24], value=12,
                             format_func=lambda h: f"{h} hours")
    use_nse = st.toggle("Also try NSE announcements (BSE always included)", value=True)
    max_items = st.slider("Most items to analyse", 15, 60, 40, step=5)

if refresh:
    build_brief.clear()

with st.spinner("Reading news and exchange filings, then analysing. This takes about a minute."):
    brief = build_brief(hours, use_nse, max_items)

c = brief["counts"]
st.markdown(
    f'<div class="stamp">Updated {brief["generated_at"].strftime("%d %b %Y, %H:%M")} IST. '
    f'{c["india"]} Indian and {c["world"]} international headlines, '
    f'{c["filings_kept"]} of {c["filings_total"]} exchange filings kept as important.</div>',
    unsafe_allow_html=True,
)

ai = brief["ai"] or {}
by_ref = {it["ref"]: it for it in brief["pool"]}
items = [i for i in ai.get("items", []) if isinstance(i, dict)]

if brief["ai_error"]:
    st.warning(brief["ai_error"])

if ai.get("market_mood"):
    st.markdown(f'<div class="mood">{esc(ai["market_mood"])}</div>', unsafe_allow_html=True)
if ai.get("top_points"):
    st.markdown("\n".join(f"- {p}" for p in ai["top_points"]))

if items:
    st.subheader("Sector tilt from this brief")
    chart = sector_chart(items)
    if chart is not None:
        st.altair_chart(chart, width="stretch")

    f1, f2, f3 = st.columns([1, 1, 1])
    with f1:
        impacts = st.multiselect("Impact", ["positive", "negative", "mixed", "neutral"],
                                 default=["positive", "negative", "mixed", "neutral"])
    with f2:
        all_sectors = sorted({s for i in items for s in i.get("sectors_up", []) + i.get("sectors_down", [])})
        sector = st.selectbox("Sector", ["All sectors"] + all_sectors)
    with f3:
        query = st.text_input("Search stock or word")

    def keep(i):
        if (i.get("impact") or "neutral").lower() not in impacts:
            return False
        if sector != "All sectors" and sector not in i.get("sectors_up", []) + i.get("sectors_down", []):
            return False
        if query:
            blob = json.dumps(i).lower()
            src = by_ref.get(i.get("ref"))
            if src:
                blob += json.dumps({k: str(v) for k, v in src.items()}).lower()
            return query.lower() in blob
        return True

    shown = [i for i in items if keep(i)]
    groups = {
        "All key items": shown,
        "India news": [i for i in shown if by_ref.get(i.get("ref"), {}).get("region") == "India"],
        "World news": [i for i in shown if by_ref.get(i.get("ref"), {}).get("region") == "World"],
        "Exchange filings": [i for i in shown if by_ref.get(i.get("ref"), {}).get("kind") == "filing"],
    }
    tabs = st.tabs([f"{k} ({len(v)})" for k, v in groups.items()])
    for tab, (name, group) in zip(tabs, groups.items()):
        with tab:
            if not group:
                st.caption("No items match these filters.")
            for i in group:
                st.markdown(card(i, by_ref.get(i.get("ref"))), unsafe_allow_html=True)

st.subheader("Everything fetched")
r1, r2, r3 = st.tabs(["India headlines", "World headlines", "Important filings"])
with r1:
    raw_table([x for x in brief["pool"] if x.get("region") == "India"])
with r2:
    raw_table([x for x in brief["pool"] if x.get("region") == "World"])
with r3:
    raw_table([x for x in brief["pool"] if x["kind"] == "filing"])

with st.expander("Sources and their status"):
    st.table(pd.DataFrame([{"Source": k, "Status": v} for k, v in brief["status"].items()]))

st.caption("Impact tags are AI-generated from headlines and filing summaries, not investment advice. "
           "Open the source before acting.")
