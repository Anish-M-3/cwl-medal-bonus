#!/usr/bin/env python3
"""CWL medal-bonus calculator for Clash of Clans.

Pulls a clan's current Clan War League (CWL) data from the official Supercell
API, scores every member's contribution across all 7 war days, and prints a
ranked list for assigning bonus medals. The #1 player is highlighted as the
monthly Gold Pass winner.

Scoring factors (all weights configurable in SCORING below):
  * attack stars (1-3 per hit)
  * Town-Hall differential of each attack (attacking UP rewards, DOWN penalises)
  * destruction % (small tiebreaker)
  * defense stars conceded (subtracted)
  * missed attacks (penalised)

Unlike the public cwlranking.vercel.app tool, this one DOES use the Town Hall
you attacked, because the official API exposes townhallLevel for every member
and attackerTag/defenderTag for every attack.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from urllib.parse import quote

try:
    import requests
except ImportError:  # pragma: no cover - dependency hint
    sys.exit("Missing dependency 'requests'. Run: pip install -r requirements.txt")

OFFICIAL_BASE = "https://api.clashofclans.com/v1"
# RoyaleAPI proxy: use this when your public IP is dynamic. Create your API key
# with the proxy's fixed IP (45.79.218.79) allow-listed, then run with --proxy.
PROXY_BASE = "https://cocproxy.royaleapi.dev/v1"

# Default scoring weights. Tune these to match how your clan values things.
SCORING = {
    "attack": 1.0,            # multiplier on attack stars (0-3)
    "defense": 0.0,           # defense is not part of this calculator (set >0 to re-enable)
    "th_up_bonus": 0.5,       # (raw/fair modes) added per TH level attacked ABOVE expectation
    "th_down_penalty": 0.0,   # (raw/fair modes) subtracted per TH level attacked BELOW expectation
    "th_absolute": 0.2,       # (absolute mode) added per TH level of the base hit, above the field's weakest base
    "destruction": 0.003,     # tiebreaker: 100% destruction -> +0.3
    "missed_attack_penalty": 2.0,  # subtracted per war a rostered member didn't attack
}


def min_defender_th(players: dict[str, "PlayerStats"]) -> int:
    """Weakest enemy TH hit anywhere (the baseline for 'absolute' scoring)."""
    return min((rec.defender_th for p in players.values() for rec in p.attacks), default=0)


@dataclass
class AttackRecord:
    stars: int
    destruction: float
    attacker_th: int
    defender_th: int
    max_enemy_th: int = 0  # strongest TH available in that war

    @property
    def th_diff(self) -> int:
        return self.defender_th - self.attacker_th

    def diff(self, mode: str) -> int:
        """Signed difficulty of the attack (positive = attacked UP/harder).

        raw:  defender TH - attacker TH (penalises a top-TH player for hits they
              are forced into, since they cannot attack 'up').
        fair: defender TH - min(attacker TH, strongest TH available that war).
              A top-TH player is judged against the strongest reachable target,
              so hitting the best available scores neutral (no unfair penalty),
              while reaching above your own TH still earns a bonus.
        """
        if mode == "raw":
            return self.defender_th - self.attacker_th
        expected = min(self.attacker_th, self.max_enemy_th) if self.max_enemy_th else self.attacker_th
        return self.defender_th - expected


@dataclass
class PlayerStats:
    tag: str
    name: str
    th: int = 0
    wars_rostered: int = 0
    attacks_made: int = 0
    missed_attacks: int = 0
    total_stars: int = 0
    defense_stars: int = 0
    attacked_up: int = 0
    attacked_mirror: int = 0
    attacked_down: int = 0
    attacks: list[AttackRecord] = field(default_factory=list)
    score: float = 0.0


def normalise_tag(tag: str) -> str:
    """Return a canonical '#AAA' tag (uppercased, single leading '#')."""
    tag = tag.strip().upper().lstrip("#")
    tag = tag.replace("O", "0")  # CoC tags use zero, never the letter O
    return "#" + tag


def encode_tag(tag: str) -> str:
    return quote(normalise_tag(tag), safe="")


def town_hall(member: dict) -> int:
    # War member objects use 'townhallLevel'; league-group members use 'townHallLevel'.
    return int(member.get("townhallLevel") or member.get("townHallLevel") or 0)


class CocClient:
    def __init__(self, token: str, base: str):
        self.base = base
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _get(self, path: str) -> dict:
        resp = self.session.get(f"{self.base}{path}", timeout=30)
        if resp.status_code == 403:
            sys.exit(
                "403 Forbidden: the API token is invalid or your IP is not allow-listed.\n"
                "Fix the key's allowed IPs at https://developer.clashofclans.com/#/account\n"
                "or run with --proxy (and allow-list the RoyaleAPI proxy IP on your key)."
            )
        if resp.status_code == 404:
            sys.exit("404 Not Found: clan tag is wrong, or the clan is not currently in CWL.")
        resp.raise_for_status()
        return resp.json()

    def league_group(self, clan_tag: str) -> dict:
        return self._get(f"/clans/{encode_tag(clan_tag)}/currentwar/leaguegroup")

    def war(self, war_tag: str) -> dict:
        return self._get(f"/clanwarleagues/wars/{encode_tag(war_tag)}")


def collect_stats(client: CocClient, clan_tag: str, include_live: bool = False) -> dict[str, PlayerStats]:
    clan_tag = normalise_tag(clan_tag)
    group = client.league_group(clan_tag)

    war_tags = [wt for rnd in group.get("rounds", []) for wt in rnd.get("warTags", []) if wt and wt != "#0"]
    if not war_tags:
        sys.exit("No war tags found yet. CWL may not have started its first battle day.")

    players: dict[str, PlayerStats] = {}

    def player(tag: str, name: str, th: int) -> PlayerStats:
        p = players.get(tag)
        if p is None:
            p = PlayerStats(tag=tag, name=name, th=th)
            players[tag] = p
        if th:
            p.th = th  # keep latest known TH
        return p

    for war_tag in war_tags:
        war = client.war(war_tag)
        state = war.get("state")
        # Only finished wars count. The live war (inWar) is optional and never
        # penalises "missed" attacks since players may still attack. Wars in
        # preparation / not started are ignored entirely.
        if state == "warEnded":
            count_missed = True
        elif state == "inWar" and include_live:
            count_missed = False
        else:
            continue

        clan, opponent = war.get("clan", {}), war.get("opponent", {})
        if normalise_tag(opponent.get("tag", "")) == clan_tag:
            clan, opponent = opponent, clan
        if normalise_tag(clan.get("tag", "")) != clan_tag:
            continue  # this war doesn't involve us (shouldn't happen)

        opp_th = {m["tag"]: town_hall(m) for m in opponent.get("members", [])}
        max_enemy_th = max((town_hall(m) for m in opponent.get("members", [])), default=0)

        for m in clan.get("members", []):
            p = player(m["tag"], m.get("name", m["tag"]), town_hall(m))
            p.wars_rostered += 1

            attacks = m.get("attacks") or []
            if attacks:
                for atk in attacks:
                    rec = AttackRecord(
                        stars=int(atk.get("stars", 0)),
                        destruction=float(atk.get("destructionPercentage", 0)),
                        attacker_th=town_hall(m),
                        defender_th=opp_th.get(atk.get("defenderTag"), town_hall(m)),
                        max_enemy_th=max_enemy_th,
                    )
                    p.attacks.append(rec)
                    p.attacks_made += 1
                    p.total_stars += rec.stars
            elif count_missed:
                p.missed_attacks += 1

            best = m.get("bestOpponentAttack") or {}
            p.defense_stars += int(best.get("stars", 0))

    return players


def score_components(p: "PlayerStats", weights: dict, mode: str, baseline: int = 0) -> dict:
    """Break a player's score into additive parts (used for scoring and charts)."""
    stars = p.total_stars * weights["attack"]
    th = 0.0
    for rec in p.attacks:
        if mode == "absolute":
            th += weights["th_absolute"] * (rec.defender_th - baseline)
        else:
            d = rec.diff(mode)
            if d > 0:
                th += weights["th_up_bonus"] * d
            elif d < 0:
                th -= weights["th_down_penalty"] * (-d)
    destruction = sum(weights["destruction"] * rec.destruction for rec in p.attacks)
    penalty = -(weights["defense"] * p.defense_stars + weights["missed_attack_penalty"] * p.missed_attacks)
    return {"stars": stars, "th": th, "destruction": destruction, "penalty": penalty}


def compute_player_score(p: "PlayerStats", weights: dict, mode: str, baseline: int = 0) -> float:
    c = score_components(p, weights, mode, baseline)
    return round(c["stars"] + c["th"] + c["destruction"] + c["penalty"], 4)


def score_players(players: dict[str, PlayerStats], weights: dict, mode: str = "absolute") -> None:
    baseline = min_defender_th(players) if mode == "absolute" else 0
    for p in players.values():
        p.attacked_up = p.attacked_mirror = p.attacked_down = 0
        for rec in p.attacks:
            d_info = rec.th_diff if mode == "absolute" else rec.diff(mode)
            if d_info > 0:
                p.attacked_up += 1
            elif d_info < 0:
                p.attacked_down += 1
            else:
                p.attacked_mirror += 1
        p.score = compute_player_score(p, weights, mode, baseline)


def print_report(players: dict[str, PlayerStats], bonus_slots: int, detail: bool = False,
                 mode: str = "absolute") -> None:
    ranked = sorted(players.values(), key=lambda p: p.score, reverse=True)
    if not ranked:
        print("No players found.")
        return

    print()
    print("=" * 78)
    print(" CWL MEDAL-BONUS RANKING")
    print("=" * 78)
    header = f"{'#':>2}  {'Player':<22}{'TH':>3}{'Score':>9}{'Stars':>7}{'Att':>5}{'Miss':>5}  {'TH up/mir/dn'}"
    print(header)
    print("-" * 78)
    for i, p in enumerate(ranked, 1):
        marker = " *" if i <= bonus_slots else "  "
        name = (p.name[:20] + "..") if len(p.name) > 22 else p.name
        print(
            f"{i:>2}{marker}{name:<22}{p.th:>3}{p.score:>9.4f}{p.total_stars:>7}"
            f"{p.attacks_made:>5}{p.missed_attacks:>5}"
            f"   {p.attacked_up}/{p.attacked_mirror}/{p.attacked_down}"
        )
    print("-" * 78)
    print(f"'*' = receives a bonus medal (top {bonus_slots}).")

    winner = ranked[0]
    print()
    print("#" * 78)
    print(f"  GOLD PASS WINNER (rank #1): {winner.name}  {winner.tag}   score {winner.score:.4f}")
    print("#" * 78)

    print("\nBonus-medal recipients:")
    for i, p in enumerate(ranked[:bonus_slots], 1):
        print(f"  {i}. {p.name}  ({p.tag})")
    print()

    if detail:
        labels = {
            "absolute": "TOWN HALL (absolute: base strength above the field's weakest base)",
            "fair": "TOWN HALL (fair: capped at strongest available)",
            "raw": "TOWN HALL (raw)",
        }
        baseline = min_defender_th(players) if mode == "absolute" else 0
        print("=" * 78)
        print(f" PER-ATTACK {labels[mode]} MATCHUPS")
        print("=" * 78)
        for i, p in enumerate(ranked, 1):
            print(f"{i:>2}. {p.name}  (own TH{p.th})")
            if not p.attacks:
                print("      (no attacks)")
            for rec in p.attacks:
                if mode == "absolute":
                    strength = rec.defender_th - baseline
                    note = f"+{strength} over base TH{baseline}"
                    tag = "HIT "
                else:
                    diff = rec.diff(mode)
                    tag = "UP  " if diff > 0 else ("DOWN" if diff < 0 else "MIR ")
                    note = f"+{diff}" if diff > 0 else (str(diff) if diff < 0 else "0")
                    if mode == "fair" and rec.max_enemy_th:
                        note += f"  [best avail TH{rec.max_enemy_th}]"
                print(
                    f"      {tag} TH{rec.attacker_th} -> TH{rec.defender_th}"
                    f"  {rec.stars}*  {rec.destruction:.0f}%  ({note})"
                )
            if p.missed_attacks:
                print(f"      MISSED x{p.missed_attacks}")
        print()


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="CWL medal-bonus calculator (Clash of Clans).")
    ap.add_argument("clan_tag", help="Clan tag, e.g. #2GYLQ9LLP")
    ap.add_argument("--bonus-slots", type=int, default=8, help="How many bonus medals to assign (default 8).")
    ap.add_argument("--proxy", action="store_true", help="Route via RoyaleAPI proxy (for dynamic IPs).")
    ap.add_argument("--detail", action="store_true", help="Show each player's per-attack TH matchups.")
    ap.add_argument("--th-mode", choices=("absolute", "fair", "raw"), default="absolute",
                    help="TH scoring basis: 'absolute' (default) rewards by how strong the base you hit "
                         "is (top players earn a plus too); 'fair' caps expectation at the strongest TH "
                         "available that war; 'raw' = plain attacker-vs-defender TH difference.")
    ap.add_argument("--include-live", action="store_true",
                    help="Also count the in-progress war's attacks (no missed penalty for it).")
    ap.add_argument("--token", default=os.environ.get("COC_API_TOKEN"), help="API token (or set COC_API_TOKEN).")
    for key in SCORING:
        ap.add_argument(f"--{key.replace('_', '-')}", type=float, default=SCORING[key],
                        help=f"Weight '{key}' (default {SCORING[key]}).")
    return ap.parse_args()


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines) so COC_API_TOKEN is picked up."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    # Player names contain emoji/non-Latin chars; force UTF-8 so Windows cp1252 doesn't choke.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    load_dotenv()
    args = parse_args()
    if not args.token:
        args.token = os.environ.get("COC_API_TOKEN")
    if not args.token:
        sys.exit("No API token. Set COC_API_TOKEN (see README) or pass --token.")

    weights = {k: getattr(args, k) for k in SCORING}
    base = PROXY_BASE if args.proxy else OFFICIAL_BASE
    client = CocClient(args.token, base)

    players = collect_stats(client, args.clan_tag, include_live=args.include_live)
    score_players(players, weights, mode=args.th_mode)
    print_report(players, args.bonus_slots, detail=args.detail, mode=args.th_mode)


if __name__ == "__main__":
    main()
