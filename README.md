# CWL Medal-Bonus Calculator (Clash of Clans)

Ranks your clan's **Clan War League** performance and tells you **who should get the
bonus medals** — plus the **#1 player** for your monthly Gold Pass giveaway.

Unlike the public [cwlranking.vercel.app](https://cwlranking.vercel.app/) tool, this one
factors in **which Town Hall each attack hit relative to the attacker's own TH**
(attacking up is rewarded, attacking down is penalised), because the official Supercell
API exposes `townhallLevel` for every member and `attackerTag`/`defenderTag` for every attack.

## What it scores

For every member, summed across all 7 CWL war days:

| Factor | Default weight | Effect |
|---|---|---|
| Attack stars (0–3) | `attack` = 1.0 | more stars = higher |
| TH attacked **up** | `th_up_bonus` = 0.5 / level | reward hitting higher THs |
| TH attacked **down** | `th_down_penalty` = 0.5 / level | penalise hitting lower THs |
| Destruction % | `destruction` = 0.003 | small tiebreaker (100% → +0.3) |
| Defense stars conceded | `defense` = 0.35 / star | subtracted |
| Missed attack | `missed_attack_penalty` = 2.0 | subtracted per war not attacked |

All weights are overridable from the command line (e.g. `--th-up-bonus 0.75`).

### Fair vs raw Town-Hall mode (`--th-mode`)

A top-TH player (e.g. a TH17 when TH17 is the max) **cannot attack up**, so a plain
attacker-vs-defender TH comparison unfairly penalises them for forced down-hits.

- **`absolute` (default)** — the bonus scales with **how strong the base you hit is**:
  `bonus = th_absolute × (defenderTH − weakestBaseHit)`. Top players earn a plus for
  clearing top bases, reaching higher is worth more, and cherry-picking a weak base
  earns little. The `up/mir/dn` column is informational (relative to your own TH).
- **`fair`** — the expected target is capped at the **strongest TH available that war**:
  `diff = defenderTH − min(attackerTH, strongestEnemyTH)`. Hitting the best available
  scores neutral; reaching *above* your own TH earns the bonus.
- **`raw`** — plain `defenderTH − attackerTH` (the original rule; penalises top THs).

### War states

Only **finished** wars (`state == warEnded`) are scored by default, so attacks that
aren't due yet are never counted as "missed". Add `--include-live` to also count the
in-progress war's completed attacks (without a missed penalty for it).

### Rushed-base adjustment (`--rush-adjust`)

A rushed TH18 (under-levelled heroes) is weaker than a maxed one, so clearing it
shouldn't score like a real TH18. With `--rush-adjust`, each defender's **hero
completion** (`/players/{tag}`) scales down the TH value of that base — a rushed base is
worth proportionally less. Defensive buildings aren't in the API, so heroes are used as
the strength proxy. This makes one API call per unique defender, so it's slower; it's off
by default (a checkbox in the web app).

## 1. Get a Clash of Clans API token (free)

1. Go to **https://developer.clashofclans.com/** and log in with your Supercell ID.
2. Open **My Account → Create New Key**.
3. Give it a **Name** and **Description**.
4. Under **Allowed IP Addresses**, enter your current public IPv4.
   Find it at **https://api.ipify.org**.
5. Click **Create**, then copy the long token (a JWT string).
6. Create a `.env` file (copy `.env.example`) and paste it in:
   ```
   COC_API_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGci...
   ```

### Dynamic / changing home IP? Use the proxy

Home IPs often change, which breaks the IP allow-list. Workaround — the free
**RoyaleAPI proxy**:

1. When creating the key, set **Allowed IP** to the proxy's fixed IP: `45.79.218.79`
   (verify the current value at https://docs.royaleapi.com/proxy.html).
2. Run the tool with `--proxy` so requests go through `cocproxy.royaleapi.dev`.

## 2. Install

```powershell
cd cwl-medal-bonus
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 3. Run

```powershell
# clan must be actively in CWL (during the week-long event)
python coc_cwl_bonus.py "#2GYLQ9LLP" --bonus-slots 8
```

With the proxy (dynamic IP):

```powershell
python coc_cwl_bonus.py "#2GYLQ9LLP" --proxy
```

Tune the TH weighting, e.g. reward attacking up more and punish attacking down harder:

```powershell
python coc_cwl_bonus.py "#2GYLQ9LLP" --th-up-bonus 0.75 --th-down-penalty 1.0
```

## Example output

```
==============================================================================
 CWL MEDAL-BONUS RANKING
==============================================================================
 #  Player                 TH    Score  Stars  Att Miss DefSt  TH up/mir/dn
------------------------------------------------------------------------------
 1 *champion akshay        16  10.1230     9    3    0     2   2/1/0
 2 *anish                  16   9.8700     9    3    0     3   1/2/0
 ...
------------------------------------------------------------------------------
'*' = receives a bonus medal (top 8).

##############################################################################
  GOLD PASS WINNER (rank #1): champion akshay  #JVVGYQ0Q   score 10.1230
##############################################################################

Bonus-medal recipients:
  1. champion akshay  (#JVVGYQ0Q)
  2. anish  (#...)
  ...
```

> **Note:** CWL data is only available from the official API **while CWL is running**
> (the week-long event). Outside that window the clan has no `leaguegroup` and the
> tool will report the clan is not currently in CWL.

## Disclaimer

This material is unofficial and not endorsed by Supercell. See
[Supercell's Fan Content Policy](https://supercell.com/en/fan-content-policy/).
