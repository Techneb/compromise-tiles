# Widened boxes — proposal (item 26, 2026-09-26)

One box per district city: its **whole municipality** when under ~150 km², else the **going-out core + 1 km**
(for this first version, the bounding box of its existing district squares plus 1 km). Municipal extents are the
OpenStreetMap admin boundary relation from the city's Geofabrik extract (osmium, no Overpass). `km2_city` is
`city_area_km2` from the app's `cities.csv`, else the OSM polygon's area. A box is larger than its municipality;
ratios over 2.5 are flagged. Cells: box area ÷ 0.04 km². Size: 280 KB a cell (buildings layer). Not merged into
`cities.json` yet.

| City | Rule | Box [s, w, n, e] | Box km² | City km² | Ratio | OSM relation (level) | 200 m cells | Buildings size | Note |
|---|---|---|---:|---:|---:|---|---:|---:|---|
| Valencia | municipality | 39.2784, -0.4326, 39.5666, -0.2725 | 438.7 | 138.7 | 3.2 ⚑ | 344953 (8) | 10,968 | 3.07 GB | the municipality runs south to the Albufera pedanías (El Palmar, El Saler), 30 km of coast |
| Vigo | municipality | 42.1324, -8.9183, 42.2647, -8.6270 | 351.4 | 109.0 | 3.2 ⚑ | 341381 (8) | 8,786 | 2.46 GB | the Cíes islands belong to the municipality and widen the box 20 km west |
| Seville | municipality | 37.3002, -6.0329, 37.4530, -5.8192 | 319.4 | 140.8 | 2.3 | 342563 (8) | 7,985 | 2.24 GB |  |
| San Francisco | municipality | 37.7065, -122.5293, 37.8631, -122.3576 | 261.6 | 121.4 | 2.2 | 111968 (6) | 6,540 | 1.83 GB | trimmed: buildings inside the city-county boundary's mainland part; the raw boundary box is [37.6403, -123.1738, 37.9297, -122.2815], 2,512 km² (Farallones, bay water) |
| Barcelona | municipality | 41.3170, 2.0525, 41.4679, 2.2284 | 245.1 | 101.4 | 2.4 | 347950 (8) | 6,128 | 1.72 GB |  |
| Bologna | municipality | 44.4211, 11.2296, 44.5561, 11.4334 | 241.6 | 140.9 | 1.7 | 43172 (8) | 6,040 | 1.69 GB |  |
| Copenhagen | municipality | 55.6129, 12.4530, 55.7327, 12.7342 | 233.8 | 86.4 | 2.7 ⚑ | 2192363 (7) | 5,846 | 1.64 GB | the municipality's own two parts (city + Amager) span sea and the airport side; Frederiksberg (own municipality) sits inside the box |
| Turin | municipality | 45.0068, 7.5778, 45.1402, 7.7733 | 226.7 | 130.0 | 1.7 | 43992 (8) | 5,668 | 1.59 GB |  |
| Las Palmas | municipality | 28.0244, -15.5272, 28.1814, -15.3949 | 225.5 | 103.5 | 2.2 | 340783 (8) | 5,639 | 1.58 GB |  |
| The Hague | municipality | 52.0148, 4.1850, 52.1350, 4.4225 | 216.0 | 98.1 | 2.2 | 192736 (8) | 5,400 | 1.51 GB |  |
| San Sebastián | municipality | 43.2178, -2.0868, 43.3382, -1.8879 | 214.6 | 60.4 | 3.6 ⚑ | 346465 (8) | 5,365 | 1.50 GB | the municipality stretches west over Igeldo and inland; the city itself is its east quarter |
| Toulouse | municipality | 43.5327, 1.3503, 43.6687, 1.5153 | 200.0 | 118.3 | 1.7 | 35738 (8) | 5,001 | 1.40 GB |  |
| Utrecht | municipality | 52.0263, 4.9701, 52.1421, 5.1952 | 197.2 | 99.2 | 2.0 | 419203 (8) | 4,929 | 1.38 GB |  |
| Strasbourg | municipality | 48.4919, 7.6881, 48.6462, 7.8361 | 186.0 | 78.3 | 2.4 | 71033 (8) | 4,650 | 1.30 GB |  |
| Athens | municipality | 37.9488, 23.6870, 38.0328, 23.7904 | 84.3 | 39.0 | 2.2 | 1370736 (7) | 2,107 | 0.59 GB |  |
| Bilbao | municipality | 43.2137, -2.9860, 43.2901, -2.8803 | 72.4 | 40.4 | 1.8 | 339549 (8) | 1,810 | 0.51 GB |  |
| Melbourne | municipality | -37.8507, 144.8970, -37.7755, 144.9913 | 69.0 | 37.7 | 1.8 | 2404870 (6) | 1,724 | 0.48 GB |  |
| Chicago | core | 41.8705, -87.6957, 41.9610, -87.6132 | 68.4 | 606.1 | 0.1 | 122604 (8) | 1,710 | 0.48 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Berlin | core | 52.4874, 13.3794, 52.5496, 13.4801 | 46.9 | 891.3 | 0.1 | 62422 (4) | 1,173 | 0.33 GB | Berlin is a Land (level 4) and a municipality at once |
| Basel | municipality | 47.5193, 7.5547, 47.5899, 7.6341 | 46.6 | 23.9 | 1.9 | 1683619 (8) | 1,165 | 0.33 GB |  |
| London | core | 51.4981, -0.1643, 51.5526, -0.0563 | 45.1 | 1,572.0 | 0.0 | 175342 (5) | 1,127 | 0.32 GB | Greater London (level 5); boroughs are level 8 |
| New York | core | 40.7038, -74.0205, 40.7472, -73.9392 | 32.9 | 783.8 | 0.0 | 175905 (5) | 823 | 0.23 GB | New York City (level 5, 1,211 km² with water) |
| Geneva | municipality | 46.1778, 6.1102, 46.2319, 6.1758 | 30.2 | 15.9 | 1.9 | 1685488 (8) | 756 | 0.21 GB |  |
| Vienna | core | 48.1849, 16.3338, 48.2301, 16.3982 | 23.9 | 414.8 | 0.1 | — (4) | 597 | 0.17 GB | the Wien relation (level 4) is incomplete in the alps extract; 414.8 km² (cities.csv) decides core anyway |
| Amsterdam | core | 52.3414, 4.8609, 52.3906, 4.9226 | 22.8 | 219.3 | 0.1 | 47811 (8) | 571 | 0.16 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Budapest | core | 47.4664, 19.0301, 47.5176, 19.0834 | 22.7 | 525.2 | 0.0 | 1244004 (8) | 568 | 0.16 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Warsaw | core | 52.2059, 20.9902, 52.2633, 21.0425 | 22.6 | 517.2 | 0.0 | 336074 (8) | 566 | 0.16 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Madrid | core | 40.3974, -3.7277, 40.4406, -3.6798 | 19.4 | 604.3 | 0.0 | 5326784 (8) | 485 | 0.14 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Málaga | core | 36.7024, -4.4398, 36.7370, -4.4007 | 13.3 | 393.6 | 0.0 | 340746 (8) | 334 | 0.09 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Rotterdam | core | 51.9024, 4.4557, 51.9361, 4.5053 | 12.7 | 324.1 | 0.0 | 324431 (8) | 318 | 0.09 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Palma | core | 39.5548, 2.6208, 39.5848, 2.6618 | 11.7 | 207.6 | 0.1 | 341321 (8) | 292 | 0.08 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Wrocław | core | 51.0949, 17.0015, 51.1236, 17.0535 | 11.5 | 292.8 | 0.0 | 2805690 (8) | 289 | 0.08 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Zaragoza | core | 41.6411, -0.9000, 41.6683, -0.8640 | 9.0 | 968.9 | 0.0 | 345740 (8) | 226 | 0.06 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Murcia | core | 37.9710, -1.1502, 37.9982, -1.1160 | 9.0 | 881.2 | 0.0 | 340611 (8) | 226 | 0.06 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Alicante | core | 38.3319, -0.4977, 38.3591, -0.4633 | 9.0 | 201.7 | 0.0 | 342792 (8) | 226 | 0.06 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Córdoba | core | 37.8709, -4.7967, 37.8981, -4.7625 | 9.0 | 1,248.9 | 0.0 | 343207 (8) | 226 | 0.06 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Valladolid | core | 41.6385, -4.7465, 41.6657, -4.7105 | 9.0 | 196.1 | 0.0 | 348849 (8) | 226 | 0.06 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |
| Gijón | core | 43.5314, -5.6812, 43.5586, -5.6440 | 9.0 | 182.8 | 0.0 | 345576 (8) | 226 | 0.06 GB | districts' own squares + 1 km (first version, to be replaced by going-out cells + 1 km) |

**Total: 38 cities, 106,716 cells, ~29.9 GB of buildings at 280 KB a cell**
(19 whole municipalities: 96,507 cells; 19 cores: 10,209 cells). ⚑ = box over 2.5 × the municipality.

A box counts its sea, fields and neighbours as cells; a build over a municipality box can keep only the cells
inside the boundary (the `departements` filter does this for France) to cut the volume toward the km² column:
clipped so, the list would be ~52,292 cells, ~14.6 GB.
