# Sims 3 Sims3Pack repair

Repairs the legacy package headers inside old Sims 3 Store downloads, preserving the original files and item data.

## Installation error

```text
IAMs2011EP5Reg.Sims3Pack: Installation failed: Please make sure your game has the latest Software Updates and try again.
```

This occurred on game version **1.69.47.024017**. Repairing the headers changed the Iams installer's result to **Success!**; resetting the launcher cache did not resolve the original error.

**Current limitation:** all seven files passed recovery checks, but the owner reports missing content after loading a world and the launcher returning to an uninstalled state. The new database contains only bookkeeping data. **Header repair alone has not fixed in-game availability.** Investigation is ongoing.

## Affected downloads and IDs

PackageIds are internal archive identifiers, not redemption codes. Launcher installation of the other six sets remains untested.

| Sims3Pack filename | Content | PackageId |
| --- | --- | --- |
| IAMs2011EP5Reg.Sims3Pack | Iams Pet Starter Kit | 0x8d44177122e145928b47ac1ca1ad7859 |
| EP5RegSet.Sims3Pack | Atomic Age Pets Registration Gifts | 0x45926671b7a04473accc05011fab68d7 |
| EP7LimitedEditionSET.Sims3Pack | Plants vs. Zombies Content Pack | 0xe2a5a3b708b74ccb8c4d70ac5c86e366 |
| EP8RegSET.Sims3Pack | Seasons Wall Decal Pack | 0x78b6efdaca0a4c22b860bc1021671b84 |
| PetStoreEP5Set.Sims3Pack | Graham's Pet Swap Shop | 0x406012129fa042eb8d8bf6f9684e9da1 |
| RenaultSet2.Sims3Pack | EV Pack + Renault Twizy + Renault Fluence | 0x4af31976afed4aa4bc8e95fa4332fc9a |
| Riverview.Sims3Pack | Riverview world | 0xd535a1c25e231c854401ac15e7ae1bb2 |

## Run the repair on Windows

### 1. Prepare

- Close the game and launcher. Back up `Documents\Electronic Arts\The Sims 3`.
- Install [Python 3.10 or newer](https://www.python.org/downloads/windows/).
- [Download and extract the project ZIP](https://github.com/murlock1000/sims3-legacy-content-repair/archive/refs/heads/main.zip). Place the folder containing `sims3pack_repair.py` at `C:\Users\<Windows user>\Sims3PackRepair`.

`%USERPROFILE%` opens `C:\Users\<Windows user>` when entered in File Explorer's address bar. Original downloads are normally in `Documents\Electronic Arts\The Sims 3\Downloads`; Documents may be under OneDrive.

### 2. Run in PowerShell

```powershell
Set-Location "$env:USERPROFILE\Sims3PackRepair"
$sims = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Electronic Arts\The Sims 3'
New-Item -ItemType Directory -Path ".\input" -Force
Copy-Item -Path "$sims\Downloads\*.Sims3Pack" -Destination ".\input"
py -3 sims3pack_repair.py recover ".\input" --out ".\recovered"
```

`$sims` selects the Windows Documents profile, including redirected Documents folders. For a different profile, replace that line with its full path, such as `$sims = 'D:\Documents\Electronic Arts\The Sims 3'`.

Results are written to `%USERPROFILE%\Sims3PackRepair\recovered`. Each file reports `"status": "ok"` or `"status": "error"` with a reason. An `ok` result confirms recovery, not installation.

## Test installation

### 3. Install and check

Open `%USERPROFILE%\Sims3PackRepair\recovered` and double-click the repaired file, for example `IAMs2011EP5Reg.repaired.Sims3Pack`.

Keep originals in a separate backup folder: the launcher lists original and repaired files under the same content name when both are in its Downloads folder.

Check the content in a new test save: **Buy Mode** for objects, **Create a Sim** for clothing, the **new-game world list** for Riverview, and the **Edit Town library** for venues. A launcher Success message alone is insufficient.

For ordinary objects or clothing missing after installation, use the [standard package setup](https://modthesims.info/wiki.php?title=Game_Help:Installing_Sims_3_Package_Files/Setup_and_Files), including `Mods\Resource.cfg`, then copy the set's folder:

```text
From: %USERPROFILE%\Sims3PackRepair\recovered\Packages\IAMs2011EP5Reg\
To:   <Documents>\Electronic Arts\The Sims 3\Mods\Packages\IAMs2011EP5Reg\
```

For Iams, the destination contains seven `.package` files. `<Documents>` means the Documents folder used by the game. Use one installation route at a time in the test profile.

**Riverview and the complete pet-store venue require launcher installation.** World and lot files are excluded from the package export; the pet-store export supplies only its ordinary objects.

## Further details

This repair does not resolve missing expansions, script/core mod conflicts, or every cause of the update error. For help, [open an issue](https://github.com/murlock1000/sims3-legacy-content-repair/issues) with the filename, game version, command result, and exact installer error. Remove personal paths from reports and exclude game assets and account files.

[Iams investigation](docs/iams-case-study.md) · [Collection results](docs/collection-results.json) · [Contributing and tests](CONTRIBUTING.md)
