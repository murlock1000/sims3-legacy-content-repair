# Sims 3 Sims3Pack repair

Converts supported old Sims 3 Store downloads so their content can install and appear in game. Original files are preserved; game assets are not included.

## Errors addressed

```text
IAMs2011EP5Reg.Sims3Pack: Installation failed: Please make sure your game has the latest Software Updates and try again.
```

Also investigated: the launcher reports **Success**, but objects are missing and downloads return to **uninstalled** after playing. In this case, header repair alone created an almost empty installation database. Conversion must also update Store metadata and linked resource references.

Tested on **1.69.47.024017**: the owner sees the **Iams Pet House**, and its content remains installed after exiting. Pets registration gifts and Seasons decals installed with their content present in the database; their in-game checks remain pending. Other causes of this error require separate fixes.

## Downloads and IDs

PackageIds identify archives; they are not redemption codes. Public conversion currently supports the three sets marked **Convert**. The others remain under investigation.

| Sims3Pack filename | Content | PackageId | Status |
| --- | --- | --- | --- |
| IAMs2011EP5Reg.Sims3Pack | Iams Pet Starter Kit | 0x8d44177122e145928b47ac1ca1ad7859 | Convert |
| EP5RegSet.Sims3Pack | Atomic Age Pets Registration Gifts | 0x45926671b7a04473accc05011fab68d7 | Convert |
| EP8RegSET.Sims3Pack | Seasons Wall Decal Pack | 0x78b6efdaca0a4c22b860bc1021671b84 | Convert |
| EP7LimitedEditionSET.Sims3Pack | Plants vs. Zombies Content Pack | 0xe2a5a3b708b74ccb8c4d70ac5c86e366 | Investigating |
| PetStoreEP5Set.Sims3Pack | Graham's Pet Swap Shop | 0x406012129fa042eb8d8bf6f9684e9da1 | Investigating |
| RenaultSet2.Sims3Pack | EV Pack + Renault Twizy + Renault Fluence | 0x4af31976afed4aa4bc8e95fa4332fc9a | Investigating |
| Riverview.Sims3Pack | Riverview world | 0xd535a1c25e231c854401ac15e7ae1bb2 | Investigating |

## Windows instructions

1. Close the game and launcher. Back up `Documents\Electronic Arts\The Sims 3`.
2. Install [Python 3.10+](https://www.python.org/downloads/windows/). [Download and extract this project](https://github.com/murlock1000/sims3-legacy-content-repair/archive/refs/heads/main.zip) into `%USERPROFILE%\Sims3PackRepair`. The folder should contain `sims3pack_repair.py`, `sims3pack_formats.py` and `store_conversion.py`.
3. Open PowerShell and run:

```powershell
Set-Location "$env:USERPROFILE\Sims3PackRepair"
$sims = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Electronic Arts\The Sims 3'
py -3 sims3pack_repair.py convert "$sims\Downloads\IAMs2011EP5Reg.Sims3Pack" --out ".\fixed"
```

Replace the filename for another supported set, or use a folder to process supported originals together. `$sims` uses Windows' Documents location, including OneDrive redirection. For another game profile, set its actual path, for example `$sims = 'D:\Documents\Electronic Arts\The Sims 3'`.

4. Open `%USERPROFILE%\Sims3PackRepair\fixed` in File Explorer. Double-click `IAMs2011EP5Reg.converted.Sims3Pack`, install it, and click **OK** when finished. Keep the original archive outside the game's Downloads folder to avoid duplicate launcher entries.
5. Check a test save. Find the **Iams Pet House** under **Buy Mode → Sort by Function → Pets → All**. Use **All Content**; Store-only filters may hide converted items.

A command result of `"status": "error"` means conversion failed; read its `reason`. `"status": "ok"` confirms conversion, not every item's gameplay behavior. The older `recover` command only repairs headers and is insufficient for this case. Use one installation route per set; remove backed-up older test packages before using the converted archive.

[Investigation](docs/iams-case-study.md) · [Results](docs/collection-results.json) · [Tests and contributions](CONTRIBUTING.md)
