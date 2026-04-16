# Vote Cross-Referencing — Conflict of Interest Flags

**Date:** 2026-04-16
**Status:** Design (approved)
**Depends on:** politics-viz base (HATVP ingest, party matching, dashboard)

## Goal

Ingest AN roll-call votes from NosDéputés, tag them by economic sector,
cross-reference with députés' declared holdings, and surface "potential
conflict of interest" flags — cases where a député voted in the interest
of a sector they hold financial positions in.

## Non-goals

- LLM-based vote classification (future enhancement; keyword-only for v1).
- Sénat votes (NosSénateurs exists but is less maintained; AN only).
- `/votes` browse page (v2; flags surface on dashboard + declarant page).
- Legal or editorial judgment — flags are mechanical co-occurrences with
  a direction heuristic, not accusations. The UI carries a disclaimer.

## Data source

NosDéputés legislature 16 API:
- `https://www.nosdeputes.fr/16/scrutins/json` — 4,105 scrutins with
  title, date, type, outcome, vote counts.
- `https://www.nosdeputes.fr/16/scrutin/{n}/json` — per-député positions
  (parlementaire_slug, position: pour/contre/abstention, groupe).

Matching to our `declarant` table: by `parlementaire_slug` → `person_key`
(same accent-folded, lowercased name matching used for party enrichment).

## Schema (new tables)

```sql
CREATE TABLE IF NOT EXISTS vote (
    id               INTEGER PRIMARY KEY,
    scrutin_numero   INTEGER NOT NULL UNIQUE,
    date             TEXT NOT NULL,
    type             TEXT,
    titre            TEXT NOT NULL,
    sort             TEXT,           -- 'adopté' / 'rejeté'
    n_pour           INTEGER,
    n_contre         INTEGER,
    n_abstention     INTEGER
);

CREATE TABLE IF NOT EXISTS vote_position (
    id            INTEGER PRIMARY KEY,
    vote_id       INTEGER NOT NULL REFERENCES vote(id),
    declarant_id  INTEGER NOT NULL REFERENCES declarant(id),
    position      TEXT NOT NULL,    -- 'pour' / 'contre' / 'abstention'
    groupe        TEXT
);

CREATE TABLE IF NOT EXISTS vote_sector_tag (
    id         INTEGER PRIMARY KEY,
    vote_id    INTEGER NOT NULL REFERENCES vote(id),
    sector     TEXT NOT NULL,
    direction  TEXT NOT NULL        -- 'pro' / 'anti' / 'neutral'
);

CREATE TABLE IF NOT EXISTS company_sector (
    company_slug  TEXT NOT NULL,
    sector        TEXT NOT NULL,
    source        TEXT,             -- 'keyword' / 'manual'
    PRIMARY KEY (company_slug, sector)
);

CREATE TABLE IF NOT EXISTS conflict_flag (
    id            INTEGER PRIMARY KEY,
    declarant_id  INTEGER NOT NULL REFERENCES declarant(id),
    vote_id       INTEGER NOT NULL REFERENCES vote(id),
    company_slug  TEXT NOT NULL,
    sector        TEXT NOT NULL,
    score         INTEGER NOT NULL, -- evaluation_eur of the holding
    reason        TEXT NOT NULL     -- human-readable explanation
);
```

Indexes: vote_position(vote_id), vote_position(declarant_id),
vote_sector_tag(vote_id), conflict_flag(declarant_id),
conflict_flag(vote_id).

## Sectors (~15 buckets)

Defined in `parsers/sectors.py` as a dict of `Sector` dataclasses:

```python
@dataclass
class SectorDef:
    slug: str
    label: str
    title_keywords: list[str]       # match against vote titre (FR)
    pro_keywords: list[str]         # if titre also matches these → direction='pro'
    anti_keywords: list[str]        # if titre also matches these → direction='anti'
    company_keywords: list[str]     # match against company_name for company_sector
    manual_companies: list[str]     # explicit company_slug overrides
```

Sectors:

| Slug | Label | Title keywords (sample) | Manual companies |
|---|---|---|---|
| energy | Énergie | énergie, nucléaire, renouvelable, pétrole, gaz, électricité, carburant | total-energies, edf, engie |
| banking | Banque & Finance | banque, crédit, financier, épargne, assurance, bancaire | credit-agricole, bnp-paribas, societe-generale, axa |
| pharma | Santé & Pharma | santé, médicament, pharmaceut, hôpital, sécurité sociale | sanofi |
| defense | Défense | défense, armée, militaire, armement | thales, dassault |
| tech | Numérique | numérique, données, intelligence artificielle, télécom, cyber | |
| agri | Agriculture | agricol, PAC, alimentaire, pesticide, phytosanitaire | |
| real_estate | Immobilier | immobilier, logement, foncier, urbanis, copropriété | |
| transport | Transport | transport, ferroviaire, aérien, SNCF, autoroute | |
| luxury | Luxe | luxe, mode, cosmétique | lvmh, hermes-international, kering, l-oreal |
| construction | BTP | BTP, construction, bâtiment, infrastructure, travaux publics | bouygues, vinci |
| media | Médias | audiovisuel, presse, média, communication | |
| telecom | Télécom | télécom, fibre, mobile, fréquence, spectre | |
| insurance | Assurance | assurance, mutuelle, prévoyance | axa |
| auto | Automobile | automobile, véhicule, mobilité | |
| food | Agroalimentaire | agroalimentaire, alimentaire, restauration, grande distribution | |

Direction heuristics (shared across all sectors):
- **pro**: accélérat, soutien, développ, favoris, allèg, simplif, exonér,
  subvention, aide, crédit d'impôt
- **anti**: régulat, taxe, contrôle, encadr, sanction, interdic, limit,
  redevance, contribut, plafon

If a vote title matches a sector's keywords AND matches pro keywords →
direction='pro'. Matches anti keywords → direction='anti'. Neither or
both → direction='neutral'.

## Company → sector mapping

Two-pass:
1. **Keyword scan**: for each company_slug in the `holding` table, scan
   the company_name against each sector's `company_keywords`. E.g.,
   company_name contains "énergie" → sector=energy.
2. **Manual overrides**: `manual_companies` list in each SectorDef maps
   known CAC40 slugs directly. These take precedence.

Result populates `company_sector` table.

## Scoring / flagging logic

A `conflict_flag` is emitted when ALL THREE hold:

1. **Holding**: declarant holds a company in sector S (from `holding` ×
   `company_sector`), with `evaluation_eur > 0`.
2. **Vote tagged**: a vote is tagged sector S with direction D (from
   `vote_sector_tag`).
3. **Aligned position**: declarant voted in the direction that benefits
   their holding:
   - direction='pro'  AND position='pour'   → flagged
   - direction='anti' AND position='contre' → flagged
   - All other combos → not flagged

`score` = SUM(evaluation_eur) of all holdings in sector S for that
declarant. This weights the flag by financial exposure.

`reason` = human-readable French string, e.g.:
*"Détient 200 000 € dans TotalEnergies (énergie). A voté pour
'accélération des énergies renouvelables' (pro-énergie)."*

## Sync commands

- `sync.py votes` — fetch all scrutins from NosDéputés (paginated by
  scrutin number). For each scrutin, fetch per-député positions. Match
  `parlementaire_slug` to `declarant` rows via person_key. Idempotent
  on `scrutin_numero`.
- `sync.py sectors` — run keyword tagger on all vote titles → populate
  `vote_sector_tag`. Run company-name scan + manual overrides → populate
  `company_sector`. Idempotent (DELETE + re-insert).
- `sync.py conflicts` — cross-reference holdings × vote_positions ×
  sector tags → populate `conflict_flag`. Idempotent (DELETE + re-insert).
- `sync.py all` gains these three steps after the existing hatvp →
  parties → changes pipeline.

Vote fetching is the slow step (~4100 individual API calls to get per-
député positions). Batch with httpx async or sequential with a polite
200ms sleep. Cached in SQLite after first run; subsequent runs only
fetch new scrutin numbers.

## UI changes

### Dashboard — new 5th card: "Potential conflicts"

Table showing top 10 députés by total flag score. Columns:

| Name | Party | Flagged votes | Holding € (flagged sectors) |
|---|---|---|---|

Click row → `/declarant/{id}`.

Card carries a disclaimer banner:
*"Flags are mechanical co-occurrences between declared holdings and
sector-related votes. They indicate potential conflicts of interest,
not wrongdoing."*

Respects the scope and window selectors already present.

### Declarant page — new section: "Voting vs. Holdings"

Shown between the change-log and the declarations list. Contains:

1. **Flagged votes** (if any): table of conflict_flag rows for this
   declarant, showing vote date, vote title (linked), company, sector,
   score, reason string. Sorted by score DESC.
2. **Full voting record**: collapsible table of all `vote_position` rows
   for this declarant, with sector tags as pills. Non-flagged votes in
   muted style; flagged ones highlighted.

### No new page for v1

`/votes` browse page is deferred. Flags are accessible via the dashboard
card and the declarant page.

## Acceptance criteria

1. `sync.py votes` ingests all 4,105 scrutins with per-député positions
   in under 30 minutes (network-bound; ~200ms per scrutin).
2. Re-running `sync.py votes` on unchanged data fetches only new scrutins.
3. `sync.py sectors` tags ≥50% of scrutins with at least one sector.
4. `sync.py conflicts` produces a non-empty `conflict_flag` table.
5. Dashboard "Potential conflicts" card shows top 10 flagged députés with
   non-zero scores.
6. Declarant page for a flagged député shows their flagged votes with
   reason strings.
7. Disclaimer text is visible on the dashboard conflict card.

## Out of scope

- LLM-based classification of votes.
- Sénat votes.
- `/votes` browse page.
- "Overengagement" scoring (volume of sector votes vs. chamber average).
- Editorial judgment on vote direction beyond keyword heuristics.
