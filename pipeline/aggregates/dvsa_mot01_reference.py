"""Pinned DVSA MOT-01 published figures: the external comparator for D7.

Source: DVSA table MOT-01 "MOT test results by class of vehicle", from
https://www.gov.uk/government/statistical-data-sets/mot-testing-data-for-great-britain
CSV retrieved 2026-08-12,
sha256 2702f210aaff0ce792a72aca9bbfb21f5d0b6978a144379d07e14d109a3b7fb2.

Values are annual ("Total" period) rows, verbatim: (tests, prs, fails).
`tests` is DVSA's initial-test count; initial fail rate = (fails+prs)/tests,
final fail rate = fails/tests -- the definitions in the MOT testing data user
guide v5.1. They are pinned as literals rather than shipped as a CSV because
the repo does not track *.csv (.gitignore:25) and because literals are
reviewable in a diff, which an opaque data blob is not.

Refresh procedure: re-download the CSV, diff against these literals, and update
deliberately. `published_stats_gate.load_published` reads the CSV when one is
supplied so a refresh can be verified before it is pinned.

COVERAGE: the published table begins at financial year 2013-14, so lake years
2005-2012 have no comparator here (DVSA Effectiveness Reports would be needed).

DATA-QUALITY NOTE: in the retrieved file, rows for FY2024-25 and FY2025-26
carry class labels that do not match their magnitudes -- the row labelled
"Class 5: Private passenger vehicles with more than 12 seats" holds ~33.1M
tests, which is unmistakably Classes 3 & 4, while the row labelled
"Classes 3 & 4" holds ~41k. Those financial years are therefore EXCLUDED from
the pinned set. They are outside the lake's coverage (which ends at 2023) in
any case, so nothing is lost; do not add them without resolving the labelling.
"""
from typing import Dict, Tuple

# (financial_year_start, class_group) -> (tests, prs, fails)
MOT01_ANNUAL: Dict[Tuple[int, str], Tuple[int, int, int]] = {
    (2013, "C1&2"): (1027707, 82650, 122345),
    (2013, "C3&4"): (27481013, 2561673, 8424279),
    (2013, "C5"): (47044, 3406, 13576),
    (2013, "C7"): (601942, 56557, 246213),
    (2014, "C1&2"): (1008577, 77979, 115969),
    (2014, "C3&4"): (27688292, 2542733, 8056025),
    (2014, "C5"): (44805, 3258, 11673),
    (2014, "C7"): (613769, 59870, 240534),
    (2015, "C1&2"): (1003500, 75212, 107048),
    (2015, "C3&4"): (28027320, 2474710, 7827865),
    (2015, "C5"): (45611, 3121, 11433),
    (2015, "C7"): (644353, 61547, 239971),
    (2016, "C1&2"): (1011080, 75240, 103734),
    (2016, "C3&4"): (28684053, 2451012, 7699812),
    (2016, "C5"): (47853, 3175, 11997),
    (2016, "C7"): (680461, 64691, 244496),
    (2017, "C1&2"): (968338, 68982, 96408),
    (2017, "C3&4"): (28877225, 2384216, 7571216),
    (2017, "C5"): (47816, 2980, 11500),
    (2017, "C7"): (700659, 64986, 241378),
    (2018, "C1&2"): (980543, 68778, 97204),
    (2018, "C3&4"): (29560831, 2198630, 7731619),
    (2018, "C5"): (47862, 2719, 11555),
    (2018, "C7"): (746539, 60538, 254800),
    (2019, "C1&2"): (934177, 65848, 90461),
    (2019, "C3&4"): (30065972, 2147690, 7381548),
    (2019, "C5"): (46665, 2477, 10793),
    (2019, "C7"): (777930, 62394, 251047),
    (2020, "C1&2"): (870559, 58434, 79710),
    (2020, "C3&4"): (30246272, 2010367, 7132338),
    (2020, "C5"): (40216, 1893, 8458),
    (2020, "C7"): (809892, 61483, 251241),
    (2021, "C1&2"): (962802, 61796, 87627),
    (2021, "C3&4"): (31617600, 1933618, 7301194),
    (2021, "C5"): (42326, 1829, 9592),
    (2021, "C7"): (890744, 64162, 274051),
    (2022, "C1&2"): (962554, 60025, 86090),
    (2022, "C3&4"): (32543026, 1943993, 7290101),
    (2022, "C5"): (42513, 1863, 9243),
    (2022, "C7"): (953267, 69036, 281244),
    (2023, "C1&2"): (979581, 57658, 85581),
    (2023, "C3&4"): (32693703, 1842550, 7502783),
    (2023, "C5"): (41330, 1856, 9227),
    (2023, "C7"): (999712, 70271, 292878),
}
