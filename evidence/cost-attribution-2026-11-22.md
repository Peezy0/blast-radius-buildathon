# Blast Radius offline cost attribution (7cafa55 selections = 0ff17d7 map)

Measured on 554 labelled commits: the official sample (30) plus held-out mutant sets mut1-mut11 (524).
Hidden-set labels are unavailable, so nothing here is attributed to the hidden set.
Cost = simulated scheduled seconds (busiest worker + 2 s/worker). Oracle = same scheduler on the failing tests only.
Totals: selected 3084.8 s, oracle 1373.9 s, excess 1710.9 s, 0 misses, 0 fallbacks.

## Guard measurement (Codex hypothesis)
Forcing scope_same=True or attr_ok=True for every commit changes the total by 0.00 s on all 554 commits.
No labelled commit has an unequal multi-line replacement or a shifted-definition scope change, and none hit the
attribute guard. On this evidence the guards cost nothing here. The hidden set could still contain multi-line edits,
but nothing labelled measures that.

## Class-dict excess by dict (dict rule rejected, fell back to whole-class users)
| dict | commits | excess s | why rule rejected |
|---|---|---|---|
| arg_types (Expression subclasses) | 40 | 153.3 | no per-key evidence: values are read only at class creation (required_args) |
| DATE_PART_MAPPING | 2 | 71.6 | no per-key evidence (import-time processing) |
| KEYWORDS (Tokenizer) | 2 | 59.4 | no per-key evidence (import-time trie build) |
| TIME_MAPPING | 6 | 56.0 | no per-key evidence (import-time trie build) |
| keys changed | 5 | 33.0 | key edit, not value edit |
| spread dicts, rule applied | 18 | 12.2 | |

## TPC-DS runtime verifier benchmark: REJECT
test_execute_tpcds is selected in 71 of 554 commits and fails in 25 (P(pass) = 0.648).
Marginal scheduled cost cost(S) - cost(S without it) on passing commits: mean 11.6 s. Expected saving per selection: 0.648 x 11.6 = 7.5 s.
Measured untraced CPU of the complete patched pytest process with children (br-collect image, python:3.13-slim digest, --network none):

| patch | marginal s | wall s | charged CPU s | result |
|---|---|---|---|---|
| mut1 m1004_code | 11.2 | 31.6 | 44.75 | 1 passed, 57 subtests |
| mut11 f1131_default | 17.7 | 21.8 | 38.62 | 1 passed, 57 subtests |
| mut5 m5057_code | 21.5 | 21.2 | 37.56 | 1 passed, 57 subtests |

Mean CPU 40.3 s against at most 21.5 s saved even on a pass, and 7.5 s expected. Verification also needs duckdb and TPC-DS fixtures in the runtime image. Rejected.


## By set
| group | n | selected s | oracle s | excess s |
|---|---|---|---|---|
| mut11 | 40 | 568.1 | 187.9 | 380.3 |
| mut3 | 40 | 354.4 | 111.6 | 242.8 |
| mut2 | 80 | 456.9 | 214.6 | 242.3 |
| mut9 | 52 | 401.0 | 189.6 | 211.4 |
| mut6 | 60 | 336.7 | 175.3 | 161.5 |
| mut1 | 60 | 350.0 | 189.6 | 160.4 |
| mut5 | 59 | 287.5 | 139.0 | 148.5 |
| mut8 | 79 | 215.7 | 72.1 | 143.7 |
| mut10 | 24 | 10.7 | 0.0 | 10.7 |
| sample | 30 | 101.7 | 94.4 | 7.4 |
| mut4 | 30 | 2.0 | 0.0 | 2.0 |

## By edit class (dominant contributor)
| group | n | selected s | oracle s | excess s |
|---|---|---|---|---|
| function-body | 78 | 822.8 | 282.7 | 540.1 |
| def-header | 39 | 652.8 | 166.6 | 486.2 |
| class-dict | 75 | 636.6 | 229.3 | 407.3 |
| class-attr | 32 | 199.3 | 44.1 | 155.2 |
| module-level | 2 | 73.6 | 0.0 | 73.6 |
| - | 44 | 150.7 | 129.1 | 21.6 |
| class-other | 1 | 10.7 | 0.0 | 10.7 |
| noop | 45 | 14.5 | 4.4 | 10.1 |
| readers | 238 | 523.8 | 517.7 | 6.1 |

## By selection path (dominant contributor)
| group | n | selected s | oracle s | excess s |
|---|---|---|---|---|
| paired_oldline | 171 | 1900.6 | 595.6 | 1304.9 |
| boolop | 23 | 256.8 | 73.5 | 183.3 |
| cmp | 14 | 149.8 | 20.3 | 129.5 |
| lambda | 15 | 80.5 | 33.3 | 47.2 |
| - | 44 | 150.7 | 129.1 | 21.6 |
| same_module | 45 | 14.5 | 4.4 | 10.1 |
| dictval | 4 | 8.1 | 0.0 | 8.1 |
| readers | 238 | 523.8 | 517.7 | 6.1 |

## Harmless vs breaking
| group | n | selected s | oracle s | excess s |
|---|---|---|---|---|
| harmless (0 failing) | 278 | 884.2 | 0.0 | 884.2 |
| breaking | 276 | 2200.5 | 1373.9 | 826.7 |

## Fallbacks
Counter()

## Top 15 excess patches
| set | commit | sel | fail | selected | oracle | excess | path | class |
|---|---|---|---|---|---|---|---|---|
| mut2 | m2062_code | 1586 | 0 | 36.8 | 0.0 | 36.8 | paired_oldline | module-level |
| mut3 | c302_classattr | 1584 | 0 | 36.8 | 0.0 | 36.8 | paired_oldline | class-dict |
| mut5 | m5029_code | 1586 | 0 | 36.8 | 0.0 | 36.8 | paired_oldline | module-level |
| mut3 | c316_classattr | 1534 | 1 | 36.8 | 2.0 | 34.8 | paired_oldline | class-attr |
| mut2 | m2034_code | 1577 | 1 | 36.8 | 2.0 | 34.8 | paired_oldline | class-attr |
| mut11 | f1116_default | 1490 | 1 | 36.8 | 2.0 | 34.8 | paired_oldline | def-header |
| mut6 | d613_dictval | 1584 | 1 | 36.8 | 2.0 | 34.8 | paired_oldline | class-dict |
| mut11 | f1114_default | 1429 | 21 | 36.8 | 2.5 | 34.4 | paired_oldline | def-header |
| mut3 | c322_classattr | 1577 | 3 | 36.8 | 2.9 | 34.0 | paired_oldline | class-dict |
| mut9 | b933_boolop | 291 | 2 | 34.8 | 2.8 | 32.0 | boolop | function-body |
| mut2 | m2020_code | 1617 | 63 | 36.8 | 6.5 | 30.3 | paired_oldline | function-body |
| mut6 | d648_dictval | 1317 | 0 | 29.5 | 0.0 | 29.5 | paired_oldline | class-dict |
| mut11 | f1128_default | 1253 | 0 | 29.1 | 0.0 | 29.1 | paired_oldline | def-header |
| mut6 | d647_dictval | 1121 | 0 | 27.8 | 0.0 | 27.8 | paired_oldline | class-dict |
| mut1 | m1010_code | 1130 | 0 | 27.5 | 0.0 | 27.5 | paired_oldline | class-dict |
