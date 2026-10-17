# Iams Pet Starter Kit installation failure

## Report and scope

An owner reported that the old promotional `IAMs2011EP5Reg.Sims3Pack` failed to install on Windows Sims 3 **1.69.47.024017**. The launcher asked for the latest software updates. The owner recalled redeeming a magazine code years earlier; the content was missing from purchase history or would not download again. That account history is user-reported, not independently verified.

The investigation worked on local copies. It did not change the game's executable, account information, saves, existing entitlement files, or existing Store content databases. This write-up omits private source paths and does not redistribute the content.

## What the archive contains

The examined archive is **1,620,364 bytes** and contains seven protected `DBPP` package members plus a PNG. An intact resource index could be uniquely identified and validated in each package. All **289 indexed resources** decoded in the recovered packages.

The [Sims3Pack format documentation](https://modthesims.info/wiki.php?title=Sims_3:SIMS3PACK) describes the outer file as metadata plus embedded members, with per-member offsets, lengths, and checksums. That structure matters here: a change to an embedded member also requires a matching outer checksum.

Recovery rebuilt the first **96 bytes** of each protected package as a readable `DBPF` header using its intact index, then recomputed the outer checksums. Across the archive, **749 byte positions changed**. Every package byte after its first 96 bytes remained unchanged, as did the resource identifiers. No resources were remapped and no decrapification was performed. Header reconstruction follows the approach visible in the [pinned reference implementation](https://gist.github.com/uyjulian/bd24b98a4c97b775c9ab/9f155828f41911cbc8eff4fc4021b45d49dd10c1); checksum math was independently implemented.

## What testing establishes

| Check | Observed result | Limit |
| --- | --- | --- |
| Original archive, after launcher cache reset | Genuine installer still reported failure | Does not isolate the internal failing branch |
| Recovered package structure | Seven valid indexes; 289 resources decoded | Format validation does not establish gameplay compatibility |
| Header-only preservation | Package bodies and resource identifiers unchanged | Dependencies and game interpretation still matter |
| Recovered archive through genuine installer | Installer reported success | Its new `.dbc` was only 144 bytes and held a bookkeeping resource |
| Existing content preservation | Original `.ebc`, `.ent`, and input archive hashes unchanged | Limited to the monitored files |
| Standard package framework | Seven recovered packages copied into `Mods/Packages/IamsRecovered`; destination hashes matched | File placement does not prove the game loaded them |
| In-game catalog and interactions | Owner reports content missing after world load and launcher entries reverting to uninstalled | Collection-level report; no independent per-item catalog verification |

The small bookkeeping database is a material limitation: the installer result does not demonstrate that seven content packages reached the game's usable content store. Ordinary object packages can instead be tested through the standard package-loading framework described in the [installation guide](../README.md#test-installation), with the same requirement to verify them in game.

A subsequent launcher check found both the original and repaired Iams archives in Downloads, explaining two entries with the same display name. The other six downloads were still originals; their recovered copies had remained outside the launcher folder. All seven originals were backed up with verified hashes, and the launcher folder was updated to contain only the seven recovered archives. A subsequent batch installer was observed running, but its final result was not captured before it closed. This does not extend the verified launcher-success claim beyond Iams.

The owner subsequently reported that installation completes, but content is missing after loading a world and the launcher again shows it as uninstalled. A new snapshot confirmed a 136-byte `.dbc` containing only one four-byte bookkeeping resource; the original Store `.ebc` and entitlement file remained unchanged. All recovered packages also retain Store-specific manifest flags and resource groups. The reference implementation handles those separately from header recovery. That distinction is a candidate explanation, not a traced game rejection condition. Header-only recovery is therefore not a demonstrated gameplay fix for this installation.

## Diagnosis and remaining uncertainty

The evidence points to a legacy package-format or validation problem in this installation path: the same resource bodies became structurally readable after header reconstruction, and the genuine installer changed its result. It does **not** establish that a newer game patch is required. It also does not prove that the file became accidentally corrupted, that an account entitlement expired, or that a particular installer check caused the rejection. The protected header representation itself is not evidence of accidental damage.

A fresh supported download would be the preferred comparison, but was unavailable in this case. No runtime trace identifying the original rejecting condition or in-game verification has been completed. Recovery is therefore a demonstrated structural result and a candidate installation route, with playability still an open check.

## What about the rest of the collection?

A separate inventory identified the following additional downloads with the same protected package signature. All six also passed header recovery, index validation, and resource decoding. These are structural recovery results, not claims that all of them fail for the same reason or have been verified in game.

| Download | Protected packages | Decoded resources | Special consideration |
| --- | ---: | ---: | --- |
| `EP5RegSet.Sims3Pack` — Atomic Age Pets Registration Gifts | 3 | 123 | Verify required game content |
| `EP7LimitedEditionSET.Sims3Pack` — Plants vs. Zombies Content Pack | 26 | 1,576 | Contains object and CAS content |
| `EP8RegSET.Sims3Pack` — Seasons Wall Decal Pack | 5 | 180 | Verify catalog entries |
| `PetStoreEP5Set.Sims3Pack` — Graham's Pet Swap Shop | 13 | 1,047 | Venue-related content needs its own installation check |
| `RenaultSet2.Sims3Pack` — EV Pack, Renault Twizy, and Renault Fluence | 11 | 480 | Verify objects and interactions |
| `Riverview.Sims3Pack` — Riverview | 27 | 8,442 | Includes a world; requires world installation handling |

Including Iams, this inventory covers **seven archives, 92 protected packages, and 12,137 decoded resources**. Source/recovered digests and counts are preserved in [collection results](collection-results.json); assets are excluded. Only the Iams archive received a genuine launcher installation test. Other mods outside these archives were not covered by that inventory. Dependency review and in-game checks remain outstanding. Worlds and venues must not be treated as interchangeable with ordinary object packages. Script/core mods need version and conflict troubleshooting rather than this header reconstruction.

For missing purchases and ordinary Store installation troubleshooting, consult the [EA Forums Store guide](https://forums.ea.com/discussions/the-sims-3-technical-issues-en/read-first-sims-3-store-guide/11157679).
