"""Streamlit web UI for the CWL medal-bonus calculator.

Reuses the scoring logic from coc_cwl_bonus.py and renders it as a mobile-friendly
web page. Run locally with:

    streamlit run streamlit_app.py

The Clash of Clans API token is read from (in order): Streamlit secrets
(COC_API_TOKEN), environment variable, or a local .env file.
"""

from __future__ import annotations

import os

import streamlit as st

from coc_cwl_bonus import (
    OFFICIAL_BASE,
    PROXY_BASE,
    SCORING,
    CocClient,
    collect_stats,
    load_dotenv,
    min_defender_th,
    score_players,
)


def get_token() -> str | None:
    load_dotenv()
    token = os.environ.get("COC_API_TOKEN")
    if not token:
        try:
            token = st.secrets["COC_API_TOKEN"]  # type: ignore[index]
        except Exception:
            token = None
    return token


st.set_page_config(page_title="CWL Medal-Bonus Calculator", page_icon="🏅", layout="wide")
st.title("🏅 CWL Medal-Bonus Calculator")
st.caption("Clan War League rankings with Town-Hall-aware scoring. Unofficial; not affiliated with Supercell.")

with st.sidebar:
    st.header("Settings")
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
    use_proxy = st.checkbox("Use RoyaleAPI proxy", value=True,
                            help="Required if your API key is allow-listed to the proxy IP.")

    with st.expander("Advanced weights"):
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

    run = st.button("Calculate", type="primary", use_container_width=True)


def render(clan_tag: str) -> None:
    token = get_token()
    if not token:
        st.error("No API token configured. Set COC_API_TOKEN in Streamlit secrets, env, or .env.")
        return

    base = PROXY_BASE if use_proxy else OFFICIAL_BASE
    client = CocClient(token, base)

    with st.spinner("Fetching Clan War League data..."):
        try:
            players = collect_stats(client, clan_tag, include_live=include_live)
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
    st.success(f"🏆 Gold Pass winner: **{winner.name}**  ({winner.tag}) — score {winner.score:.2f}")

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
    st.subheader("Ranking")
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.subheader(f"Bonus-medal recipients (top {bonus_slots})")
    st.write("  •  ".join(f"{i}. {p.name}" for i, p in enumerate(ranked[:bonus_slots], 1)))

    st.subheader("Per-attack detail")
    baseline = min_defender_th(players) if th_mode == "absolute" else 0
    for i, p in enumerate(ranked, 1):
        with st.expander(f"{i}. {p.name}  (TH{p.th})  —  score {p.score:.2f}"):
            if not p.attacks:
                st.write("No attacks.")
            for rec in p.attacks:
                if th_mode == "absolute":
                    note = f"+{rec.defender_th - baseline} over base TH{baseline}"
                else:
                    d = rec.diff(th_mode)
                    note = ("UP " if d > 0 else ("DOWN " if d < 0 else "MIRROR ")) + (f"{d:+d}" if d else "")
                st.write(f"TH{rec.attacker_th} → TH{rec.defender_th} · {rec.stars}★ · "
                         f"{rec.destruction:.0f}%  ({note})")
            if p.missed_attacks:
                st.write(f"❌ Missed ×{p.missed_attacks}")


if run:
    render(clan_tag)
else:
    st.info("Set your options in the sidebar and press **Calculate**. "
            "Data is only available while Clan War League is running.")
