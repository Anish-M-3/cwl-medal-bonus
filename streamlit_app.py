"""Streamlit web UI for the CWL medal-bonus calculator.

Reuses the scoring logic from coc_cwl_bonus.py and renders it as a mobile-friendly
web page with a Clash-themed look, charts and a mode comparison.

Run locally with:  streamlit run streamlit_app.py

The Clash of Clans API token is read from (in order): Streamlit secrets
(COC_API_TOKEN), environment variable, or a local .env file.
"""

from __future__ import annotations

import io
import os
import re

import altair as alt
import matplotlib
import pandas as pd
import streamlit as st

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import patches  # noqa: E402

from coc_cwl_bonus import (
    OFFICIAL_BASE,
    PROXY_BASE,
    SCORING,
    CocClient,
    collect_stats,
    compute_player_score,
    load_dotenv,
    min_defender_th,
    score_components,
    score_players,
)

# Swap this for any image URL you like (Supercell fan-content). Falls back to a
# themed gradient if the image fails to load.
COC_BG_URL = "https://images.unsplash.com/photo-1511512578047-dfb367046420?auto=format&fit=crop&w=1600&q=60"

MODE_COLORS = {"absolute": "#f4b63e", "fair": "#4caf50", "raw": "#b5651d"}


def get_token() -> str | None:
    load_dotenv()
    token = os.environ.get("COC_API_TOKEN")
    if not token:
        try:
            token = st.secrets["COC_API_TOKEN"]  # type: ignore[index]
        except Exception:
            token = None
    return token


def inject_style() -> None:
    st.markdown(
        f"""
        <style>
        .stApp {{
            background:
                linear-gradient(rgba(16, 24, 18, 0.88), rgba(16, 24, 18, 0.94)),
                url('{COC_BG_URL}');
            background-size: cover;
            background-attachment: fixed;
            background-position: center;
        }}
        .block-container {{ padding-top: 3.5rem; }}
        h1, h2, h3, h4 {{
            font-family: "Trebuchet MS", "Segoe UI", sans-serif;
            color: #ffd977 !important;
            text-shadow: 0 2px 6px rgba(0,0,0,0.6);
            letter-spacing: 0.5px;
        }}
        .coc-title {{
            text-align: center;
            font-size: 2.1rem;
            font-weight: 800;
            margin-top: 0.4rem;
            background: linear-gradient(90deg, #ffe08a, #f4b63e, #ffcf5c);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-shadow: none;
        }}
        .coc-sub {{ text-align:center; color:#cfe3c9 !important; margin-top:-8px; }}
        .winner-card {{
            background: linear-gradient(135deg, #3a2f0b, #5c4a12);
            border: 2px solid #f4b63e;
            border-radius: 16px;
            padding: 18px 22px;
            text-align: center;
            box-shadow: 0 6px 24px rgba(0,0,0,0.45);
            margin-bottom: 10px;
        }}
        .winner-card .name {{ font-size: 1.8rem; font-weight: 800; color: #ffe08a; }}
        .winner-card .meta {{ color: #e9d9a6; }}
        section[data-testid="stSidebar"] {{
            background: rgba(20, 30, 22, 0.92);
            border-right: 2px solid rgba(244,182,62,0.3);
        }}
        .stButton>button {{
            background: linear-gradient(135deg, #f4b63e, #e0922a);
            color: #241a05; font-weight: 700; border: 0; border-radius: 10px;
        }}
        .stDataFrame {{ border-radius: 12px; overflow: hidden; }}
        /* Hide the chart hover toolbar ("Show data"/download) so it can't trap the view. */
        [data-testid="stElementToolbar"] {{ display: none !important; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


st.set_page_config(page_title="CWL Medal-Bonus Calculator", page_icon="🏅", layout="wide")
inject_style()

st.markdown('<div class="coc-title">🏅 CWL MEDAL-BONUS CALCULATOR ⚔️</div>', unsafe_allow_html=True)
st.markdown('<div class="coc-sub">Clan War League rankings with Town-Hall-aware scoring · unofficial fan tool</div>',
            unsafe_allow_html=True)
st.write("")

with st.expander("ℹ️ Which scoring mode should I pick?", expanded=False):
    st.markdown(
        """
**All three modes score attack stars, destruction and missed attacks the same.**
They differ only in **how the Town Hall you attacked affects your score.**

| Mode | What it rewards | Mirror (same-TH) hit | Best for |
|---|---|---|---|
| **🟡 Absolute** | How **strong the base you hit** is — clearing a TH17 is worth more than a TH12, for everyone | Gets a **plus** (strong base = points) | Rewarding the players who cleared the **toughest bases** |
| **🟢 Fair** | Hitting the **strongest target available** that war; skipping to an easier one is penalised | **Neutral** (full credit, no bonus) | Clans that **hit their mirror** by discipline |
| **🟤 Raw** | **Punching up** — attacking a **higher** TH than your own | **0** (can't go up = no bonus) | Rewarding **underdogs** who hit above their TH |

---

### 👉 Pick by how your clan plays

- **🎲 Everyone attacks randomly / free-for-all** → **Absolute**
  No set strategy, so just reward whoever actually cleared the **strongest bases**.

- **🧭 Leader assigns bases by player experience & TH** → **Absolute**
  You placed your best players on the hardest bases, so "cleared a strong base = top of the list" matches your plan. Top-TH players also get credit for their mirror hits.

- **🎯 Everyone hits their own mirror (disciplined)** → **Fair**
  Hitting your mirror = full neutral credit; dropping to an easier base is penalised; your maxed players aren't punished for having no base to go "up" to.

- **🐤 You want to reward low-TH players who punch up** → **Raw**
  Only this mode gives a bonus for attacking a **higher** TH than your own.

*Tip: the **⚖️ Mode comparison** tab shows all three side-by-side so you can see how the order changes.*

---

### 🏚️ "Adjust for rushed bases" (optional toggle)

A **rushed** base (e.g. a TH18 with TH16-level heroes) is weaker than a maxed one, so
clearing it shouldn't score like a real TH18. Turn this on and each base's TH value is
**scaled down by the defender's hero development** (from the game's player API) — a rushed
base is worth proportionally less.

- **Works in all modes**, but **matters most in Absolute**, where every hit is rewarded by
  base strength. In **Fair/Raw** it only tempers the *punching-up* bonus (mirror/down hits
  earn no TH bonus, so there's nothing to scale).
- Note: only **heroes/troops** are in the API — **defensive buildings are not** — so hero
  development is used as the rushed proxy.
- It makes one extra API call per defender, so it's **a bit slower**. Off by default.
        """
    )

with st.sidebar:
    st.header("⚙️ Settings")
    clan_tag = st.text_input("Clan tag", value="#2GYLQ9LLP")
    bonus_slots = st.number_input("Bonus medal slots", min_value=1, max_value=50, value=8, step=1)
    th_mode = st.selectbox(
        "Town-Hall scoring mode",
        options=("absolute", "fair", "raw"),
        index=0,
        help="absolute: reward by base strength (top players earn a plus too). "
             "fair: capped at strongest available. raw: plain TH difference.",
    )
    include_live = st.checkbox("Include in-progress war", value=False,
                               help="Count the live war's done attacks (no missed penalty).")
    rush_adjust = st.checkbox("Adjust for rushed bases", value=False,
                              help="Scale a base's TH value by the defender's hero development, so a "
                                   "rushed high-TH base is worth less. Slower: one API call per defender.")
    use_proxy = st.checkbox("Use RoyaleAPI proxy", value=True,
                            help="Required if your API key is allow-listed to the proxy IP.")

    with st.expander("🎛️ Advanced weights"):
        weights = dict(SCORING)
        weights["attack"] = st.slider("Attack (per star)", 0.0, 3.0, SCORING["attack"], 0.05)
        weights["th_absolute"] = st.slider("TH absolute (per level above weakest base)",
                                           0.0, 1.0, SCORING["th_absolute"], 0.05)
        weights["th_up_bonus"] = st.slider("TH up bonus (fair/raw)", 0.0, 2.0, SCORING["th_up_bonus"], 0.05)
        weights["th_down_penalty"] = st.slider("TH down penalty (fair/raw)", 0.0, 2.0,
                                               SCORING["th_down_penalty"], 0.05)
        weights["destruction"] = st.slider("Destruction (per %)", 0.0, 0.02, SCORING["destruction"], 0.001)
        weights["missed_attack_penalty"] = st.slider("Missed attack penalty", 0.0, 5.0,
                                                     SCORING["missed_attack_penalty"], 0.5)
        weights["defense"] = st.slider("Defense (per star conceded)", 0.0, 1.0, SCORING["defense"], 0.05)

    run = st.button("⚔️ Calculate", type="primary", use_container_width=True)


_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF\uFE0F\u2122]",
    flags=re.UNICODE,
)


def clean_name(s: str) -> str:
    """Strip emoji/symbols matplotlib can't render (keeps Latin/Greek/Cyrillic)."""
    out = _EMOJI_RE.sub("", s).strip()
    return out or s


def make_results_png(ranked, bonus_slots, clan_tag, mode) -> bytes:
    n = len(ranked)
    fig_h = 1.6 + 0.42 * n
    fig = plt.figure(figsize=(8.4, fig_h), dpi=200)
    fig.patch.set_facecolor("#111a15")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    ax.text(0.5, 0.965, "CWL MEDAL-BONUS", color="#ffcf5c", fontsize=22,
            fontweight="bold", ha="center", va="center")
    w = ranked[0]
    ax.text(0.5, 0.925,
            f"Gold Pass Winner:  {clean_name(w.name)}    |    {clan_tag}    |    {mode} mode",
            color="#e9d9a6", fontsize=11, ha="center", va="center")

    top, bottom = 0.88, 0.03
    row_h = (top - bottom) / (n + 1)
    xr = {"rank": 0.055, "bonus": 0.105, "name": 0.145, "th": 0.66, "score": 0.80, "stars": 0.93}

    yh = top - row_h / 2
    ax.text(xr["rank"], yh, "#", color="#9fb3a6", fontsize=10, ha="center", va="center", fontweight="bold")
    ax.text(xr["name"], yh, "PLAYER", color="#9fb3a6", fontsize=10, ha="left", va="center", fontweight="bold")
    ax.text(xr["th"], yh, "TH", color="#9fb3a6", fontsize=10, ha="center", va="center", fontweight="bold")
    ax.text(xr["score"], yh, "SCORE", color="#9fb3a6", fontsize=10, ha="right", va="center", fontweight="bold")
    ax.text(xr["stars"], yh, "STARS", color="#9fb3a6", fontsize=10, ha="right", va="center", fontweight="bold")

    for i, p in enumerate(ranked, 1):
        y = top - row_h * i - row_h / 2
        if i == 1:
            ax.add_patch(patches.Rectangle((0.02, y - row_h / 2), 0.96, row_h, color="#3a2f0b", zorder=0))
        ax.text(xr["rank"], y, str(i), color="#dfe9e0", fontsize=10, ha="center", va="center")
        if i <= bonus_slots:
            ax.text(xr["bonus"], y, "★", color="#f4b63e", fontsize=11, ha="center", va="center")
        ax.text(xr["name"], y, clean_name(p.name)[:24], fontsize=10.5, ha="left", va="center",
                color="#ffe08a" if i == 1 else "#eef5ee")
        ax.text(xr["th"], y, str(p.th), color="#cfe3c9", fontsize=10, ha="center", va="center")
        ax.text(xr["score"], y, f"{p.score:.2f}", color="#ffd977", fontsize=10.5, ha="right",
                va="center", fontweight="bold")
        ax.text(xr["stars"], y, str(p.total_stars), color="#cfe3c9", fontsize=10, ha="right", va="center")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.2)
    plt.close(fig)
    return buf.getvalue()


def ranking_table(ranked, bonus_slots):
    rows = []
    for i, p in enumerate(ranked, 1):
        rows.append({
            "#": i,
            "Bonus": "⭐" if i <= bonus_slots else "",
            "Player": p.name,
            "TH": p.th,
            "Score": round(p.score, 2),
            "Stars": p.total_stars,
            "Att": p.attacks_made,
            "Miss": p.missed_attacks,
            "Up/Mir/Dn": f"{p.attacked_up}/{p.attacked_mirror}/{p.attacked_down}",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)


def score_bar_chart(ranked):
    df = pd.DataFrame([{"Player": p.name, "Score": round(p.score, 2), "TH": p.th} for p in ranked])
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusEnd=6)
        .encode(
            x=alt.X("Score:Q", title="Score"),
            y=alt.Y("Player:N", sort="-x", title=None),
            color=alt.Color("Score:Q", scale=alt.Scale(scheme="yelloworangebrown"), legend=None),
            tooltip=["Player", "Score", "TH"],
        )
        .properties(height=28 * len(df) + 20)
    )
    st.altair_chart(chart, use_container_width=True)


def component_chart(ranked, weights, mode):
    baseline = min_defender_th({p.tag: p for p in ranked}) if mode == "absolute" else 0
    rows = []
    for p in ranked[:12]:
        c = score_components(p, weights, mode, baseline)
        rows.append({"Player": p.name, "Component": "Attack stars", "Value": round(c["stars"], 2)})
        rows.append({"Player": p.name, "Component": "TH bonus", "Value": round(c["th"], 2)})
        rows.append({"Player": p.name, "Component": "Destruction", "Value": round(c["destruction"], 2)})
        rows.append({"Player": p.name, "Component": "Penalties", "Value": round(c["penalty"], 2)})
    df = pd.DataFrame(rows)
    order = [p.name for p in ranked[:12]]
    chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("Player:N", sort=order, title=None),
            y=alt.Y("Value:Q", title="Points"),
            color=alt.Color("Component:N",
                            scale=alt.Scale(domain=["Attack stars", "TH bonus", "Destruction", "Penalties"],
                                            range=["#4caf50", "#f4b63e", "#9ad3bc", "#e05d5d"])),
            tooltip=["Player", "Component", "Value"],
        )
        .properties(height=360)
    )
    st.altair_chart(chart, use_container_width=True)


def mode_comparison_chart(ranked, weights):
    players = {p.tag: p for p in ranked}
    baseline_abs = min_defender_th(players)
    top = ranked[:10]
    rows = []
    for p in top:
        for m in ("absolute", "fair", "raw"):
            b = baseline_abs if m == "absolute" else 0
            rows.append({"Player": p.name, "Mode": m, "Score": compute_player_score(p, weights, m, b)})
    df = pd.DataFrame(rows)
    order = [p.name for p in top]
    chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("Player:N", sort=order, title=None),
            xOffset="Mode:N",
            y=alt.Y("Score:Q", title="Score"),
            color=alt.Color("Mode:N", scale=alt.Scale(domain=list(MODE_COLORS), range=list(MODE_COLORS.values()))),
            tooltip=["Player", "Mode", "Score"],
        )
        .properties(height=380)
    )
    st.altair_chart(chart, use_container_width=True)


def detail_view(ranked, players, mode):
    baseline = min_defender_th(players) if mode == "absolute" else 0
    for i, p in enumerate(ranked, 1):
        with st.expander(f"{i}. {p.name}  ·  TH{p.th}  ·  score {p.score:.2f}"):
            if not p.attacks:
                st.write("No attacks.")
            for rec in p.attacks:
                if mode == "absolute":
                    note = f"+{rec.defender_th - baseline} over base TH{baseline}"
                else:
                    d = rec.diff(mode)
                    note = ("UP " if d > 0 else ("DOWN " if d < 0 else "MIRROR ")) + (f"{d:+d}" if d else "")
                rushed = f"  ·  🏚️ rushed ({rec.defender_dev*100:.0f}% dev)" if rec.defender_dev < 0.999 else ""
                st.write(f"TH{rec.attacker_th} → TH{rec.defender_th} · {rec.stars}★ · "
                         f"{rec.destruction:.0f}%  ({note}){rushed}")
            if p.missed_attacks:
                st.write(f"❌ Missed ×{p.missed_attacks}")


def render(clan_tag: str) -> None:
    token = get_token()
    if not token:
        st.error("No API token configured. Set COC_API_TOKEN in Streamlit secrets, env, or .env.")
        return

    base = PROXY_BASE if use_proxy else OFFICIAL_BASE
    client = CocClient(token, base)

    with st.spinner("Fetching Clan War League data..."):
        try:
            players = collect_stats(client, clan_tag, include_live=include_live,
                                    rush_adjust=rush_adjust)
        except SystemExit as exc:
            st.error(str(exc))
            return
        except Exception as exc:  # network / API errors
            st.error(f"Failed to fetch data: {exc}")
            return

    score_players(players, weights, mode=th_mode)
    ranked = sorted(players.values(), key=lambda p: p.score, reverse=True)
    if not ranked:
        st.warning("No players found.")
        return

    winner = ranked[0]
    st.markdown(
        f'<div class="winner-card">🏆 <span class="name">{winner.name}</span><br>'
        f'<span class="meta">Gold Pass winner · {winner.tag} · score {winner.score:.2f}</span></div>',
        unsafe_allow_html=True,
    )

    tab_rank, tab_charts, tab_modes, tab_detail = st.tabs(
        ["📊 Ranking", "📈 Charts", "⚖️ Mode comparison", "🔍 Per-attack detail"]
    )

    with tab_rank:
        ranking_table(ranked, bonus_slots)
        png = make_results_png(ranked, bonus_slots, clan_tag, th_mode)
        st.download_button("⬇️ Download ranking as PNG", data=png,
                           file_name=f"cwl-ranking-{th_mode}.png", mime="image/png",
                           use_container_width=True)
        st.subheader(f"⭐ Bonus-medal recipients (top {bonus_slots})")
        st.write("  •  ".join(f"{i}. {p.name}" for i, p in enumerate(ranked[:bonus_slots], 1)))

    with tab_charts:
        st.subheader("Scores")
        score_bar_chart(ranked)
        st.subheader("Score breakdown by component")
        st.caption("How each player's score is built: attack stars + TH bonus + destruction − penalties.")
        component_chart(ranked, weights, th_mode)

    with tab_modes:
        st.subheader("Same players, three scoring modes")
        st.caption("absolute = reward by base strength · fair = capped at strongest available · raw = plain TH diff.")
        mode_comparison_chart(ranked, weights)

    with tab_detail:
        detail_view(ranked, players, th_mode)


if run:
    render(clan_tag)
else:
    st.info("Set your options in the sidebar and press **⚔️ Calculate**. "
            "Data is only available while Clan War League is running.")
