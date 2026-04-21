# Market Expansion Data Center Research — Summary

Produced: 2026-04-20
Output CSV: `output/market_expansion.csv` (194 data rows, 27 columns)

## Per-Market Breakdown

| Market | DC Count | Notes |
|---|---|---|
| Northern Virginia, USA | 30 | Full Equinix DC1-DC22 campus, Digital Realty IAD35-39, AWS (IAD50/60/10/105), Iron Mountain VA-1, CoreSite VA1-3, QTS, CyrusOne Sterling, Sabey, DataBank, EdgeConneX, Vantage VA11-12 |
| Frankfurt, Germany | 27 | Equinix FR1-FR11x, Digital Realty FRA10-32 (incl. former Interxion), NTT Frankfurt 1-4, Telehouse, Maincubes FRA01-03, ITENOS, AWS FRA54, Microsoft FRA29, Vantage FRA11, CyrusOne |
| Dublin, Ireland | 27 | Equinix DB1-DB6x, Digital Realty DUB4-15, AWS Dublin (Clonshaugh/Tallaght/Blanchardstown), Microsoft Grange Castle DUB05-14, Google 1-2, CyrusOne, Echelon, Keppel, EdgeConneX, Servecentric, Vantage |
| Tokyo, Japan | 27 | Equinix TY2-TY12x (incl. Inzai hyperscale), KDDI Telehouse Koto/Tama, NTT Tokyo 1-12 (incl. TKY11/12 in Inzai), Colt Inzai 1-4, Digital Realty NRT10/12/14, AT TOKYO CC1-2, IIJ Shiroi |
| Sydney, Australia | 27 | AirTrunk SYD1-3, NEXTDC S1-S7, Equinix SY1-SY9x, Digital Realty SYD10-11, Macquarie IC2/IC3, Global Switch Ultimo East/West, CDC Eastern Creek 1-2, DCI, YTL |
| Phoenix, Arizona | 28 | QTS PHX1-2, CyrusOne Chandler PHX1-8 + Mesa, Iron Mountain AZP-1/2 + AZS-1, Aligned PHX-01/02, EdgeConneX PHX01, Digital Realty PHX10/11, Vantage Goodyear AZ11-14, PhoenixNAP, H5, Stream Goodyear, NTT PH1, Compass Mesa |
| Stockholm + Nordics | 28 | Stockholm: Equinix SK1/SK3, Digital Realty STO5/6, atNorth SWE02, EcoDataCenter Falun/Horndal/Stockholm, Bahnhof, Multigrid, Meta Luleå, Microsoft Gävle/Sandviken. Oslo: DigiPlex × 3, Green Mountain × 3. Helsinki: Equinix HE1/3/5/6/7, atNorth FIN02/03, Verne. |
| **Total** | **194** | |

## Country distribution
- USA: 58 (NoVA + Phoenix)
- Germany: 27
- Ireland: 27
- Japan: 27
- Australia: 27
- Sweden: 14
- Norway: 6
- Finland: 8

## Operational status distribution
- Active: 175
- Construction: 17
- Planned: 2 (NEXTDC S7 Eastern Creek, NTT Tokyo TKY12)

## Sources relied on
- **datacentermap.com** — city-level facility listings (Ashburn, Frankfurt, Dublin, Tokyo, Sydney, Phoenix, Stockholm, Helsinki)
- **baxtel.com** — operator campus listings w/ street addresses
- **datacenters.com** — per-operator provider pages
- **datacenterhawk.com** — facility details and addresses
- **equinix.com** — official facility pages for DC1-DC22 (Ashburn), FR1-FR11x (Frankfurt), DB1-DB6x (Dublin), TY2-TY12x (Tokyo), SY1-SY9x (Sydney), SK1/3 (Stockholm), HE1-7 (Helsinki)
- **digitalrealty.com** — IAD35/36/38/39 Digital Loudoun Plaza, FRA/DUB/STO/NRT campus pages
- **cyrusone.com** — Chandler AZ PHX1-8
- **airtrunk.com** — SYD1/2/3
- **nextdc.com** — S1-S7 Sydney data centres
- **coltdatacentres.net** — Inzai 1-4
- **vantage-dc.com** — Goodyear/Offenbach campus
- **atnorth.com** — Sweden + Finland sites
- **digiplex.com / green-mountain.no** — Norway sites
- **ironmountain.com** — VA-1 Manassas, AZP/AZS
- **coresite.com** — Reston VA1-VA3
- **peeringdb.com** — cross-referencing for facility addresses
- Press releases (datacenterdynamics.com, datacenterknowledge.com) for newer builds

## Coordinates
All latitude/longitude pairs at 4 decimals, derived from published street addresses of the specific facility (not city centroids). Precision is within ~1-2 km as allowed by spec; for operators with multiple adjacent buildings on the same campus (Equinix DC1/2/4/5/6 on Filigree Court; CyrusOne PHX1-8 Chandler; Vantage Goodyear AZ11-14; Colt Inzai 1-4) individual building coordinates were slightly offset within the campus footprint to differentiate rows.

## MW sizing methodology
- Real published values used where available (AirTrunk SYD1 = 130 MW, NEXTDC S4 = 350 MW, NTT TKY12 = 200 MW, Echelon DUB10 = 90 MW, Colt Inzai 3 = 27 MW, etc.)
- Where not published: reasonable estimates per spec — hyperscale AWS/Microsoft buildings 24-40 MW, Equinix colo 5-32 MW, Digital Realty colo 12-36 MW, AirTrunk 100+ MW, CyrusOne Chandler per-building 15-30 MW.

## Column policy
- Columns 1–11 (Name through Operational Status) populated for every row.
- Columns 12–27 (Savings_Per_MW through Missing_Inputs) left empty; commas preserved so the row has exactly 27 fields.
- Country values restricted to: USA, Germany, Ireland, Japan, Australia, Sweden, Norway, Finland.
- State/Province: US 2-letter, Germany "Hesse", Japan prefecture, Australia "NSW", Ireland "Leinster", Nordic countries blank or region name.
- Link: always blank, per spec.
- Operational Status: Active / Construction / Planned only.

## Duplicate check
- Verified zero name collisions with the existing 224-row database
  (`Vigilent Data Center Database (US)(Sheet1).csv`).
- Zero internal duplicates among the 194 new rows.

## Markets that hit the 20-25 target
All 7 markets reached or exceeded 25 rows. No market was padded; every row is a real operator/facility sourced from the references above.

## Known limitations
- The Stockholm-proper footprint is thinner than other metros, so the "Nordic" market includes Oslo and Helsinki plus a few Swedish hyperscale sites outside the immediate metro (Luleå, Falun, Horndal, Gävle) to reach the target count. This matches the user brief, which explicitly permitted Oslo/Helsinki inclusion.
- Coordinates for some recently-announced or in-construction facilities (Echelon DUB20 Arklow, Vantage DUB1, NEXTDC S7 Eastern Creek, CyrusOne Mesa) are approximated from the announced land parcels.
- Not every MW figure is independently verified; pre-cited operator publications were used where possible.
