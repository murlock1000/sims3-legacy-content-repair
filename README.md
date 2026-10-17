# Sims 3 legacy content recovery

Inspect locally owned `.Sims3Pack` downloads and recover readable package headers for a narrowly supported legacy Store format. Python 3.10 or later is required; there are no third-party Python dependencies.

This project began with `IAMs2011EP5Reg.Sims3Pack` failing on Windows Sims 3 **1.69.47.024017** with “Please make sure your game has the latest Software Updates and try again.” The recovered archive passed the genuine installer, but that installer produced only a bookkeeping database. Seven recovered Iams packages were subsequently placed in the standard package framework and their copied hashes verified. **In-game content availability remains unverified.** See the [Iams case study](docs/iams-case-study.md) for the evidence and limitations.

## Start with the supported download

Try a fresh download from the account that originally redeemed or purchased the item. The [EA Forums Store guide](https://forums.ea.com/discussions/the-sims-3-technical-issues-en/read-first-sims-3-store-guide/11157679) covers missing purchases, download problems, and testing a clean user folder. A launcher message about updates does not, by itself, identify the failed check.

If an old download is your only available copy, close the game and launcher, preserve the original, and work in a separate folder. Back up your Sims 3 user folder before testing installation. Game installation files and the Documents user folder serve different purposes; package content belongs under the latter.

## Audit and recover copies

Run these commands from the repository directory. On Windows, `py -3` can replace `python`.

```sh
python sims3pack_repair.py audit "input/IAMs2011EP5Reg.Sims3Pack" --report output/audit.json
python sims3pack_repair.py recover "input/IAMs2011EP5Reg.Sims3Pack" --out recovered
```

You can select several files, or explicitly select a directory containing downloads:

```sh
python sims3pack_repair.py audit "input" --report output/audit.json
python sims3pack_repair.py recover "input" --out recovered
```

Directory selection does not recurse. Keep inputs separate from outputs so a later audit does not mix originals and recovered copies. Read the results for each archive: one supported download does not establish that every mod can be recovered.

For a recovered archive, outputs include:

```text
recovered/
  IAMs2011EP5Reg.repaired.Sims3Pack
  Packages/
    IAMs2011EP5Reg/
      <original-package-name>.package
```

Recovery reconstructs the package header from an intact resource index and updates archive checksums. It preserves package bytes after the first 96 bytes and preserves resource identifiers. It does not remap resources or perform decrapification. Do not treat a readable package as proof that expansion requirements, catalog placement, scripts, or gameplay behavior are satisfied.

`audit` inventories the outer archive; `recover` also checks resource indexes and decompresses resources to validate them. Recovery writes a detailed `<set-name>.report.json`. Reports include local input/output paths: review and redact them before sharing. Existing outputs are reused only when their bytes match; conflicting files cause an error. Default limits are 256 MiB per input, 128 MiB per expanded resource, and 1 GiB of expanded resources per archive. The CLI help explains explicit limit overrides.

## Test installation

1. Test one content set at a time in a separate Sims 3 user folder or a backed-up profile. Keep your normal saves out of the test.
2. Try the recovered `.Sims3Pack` through the launcher. Check for the actual installed content; the launcher's **Success!** message alone is insufficient in the documented Iams case.
3. For ordinary object or CAS packages, the extracted `.package` files provide a separate route to test. Set up `Documents/Electronic Arts/The Sims 3/Mods/Resource.cfg` and `Mods/Packages` using the [Mod The Sims package setup guide](https://modthesims.info/wiki.php?title=Game_Help:Installing_Sims_3_Package_Files/Setup_and_Files). Copy the selected set's packages into `Mods/Packages/<set-name>/` and verify the framework actually loads content. Avoid installing the same resources through both routes in the test profile.
4. Verify each expected item in game and test its relevant interactions in a disposable save. Missing items or broken interactions remain a failed or incomplete test even if parsing and installation succeed.

Worlds, lots, and venues have additional installation requirements. Use the launcher and the content's installation instructions; do not put an extracted world or lot into `Mods/Packages` simply because its container ends in `.package`.

## Scope and reports

This utility targets the examined legacy protected Store package representation. It is not a repair tool for script/core mod compatibility, missing expansions, conflicting mods, missing resources, or arbitrary damaged archives. Auditing a collection is useful for separating those cases; it does not certify the collection as playable.

The repository contains code, documentation, and synthetic tests. Keep original/recovered game assets, account data, saves, raw investigation exports, and private logs outside it. When reporting a problem, include the utility result, game version, content name, and which validation stages you actually completed. Share assets only if you have permission to redistribute them.

Seven real local archives, containing 92 protected packages and 12,137 resources, passed structural recovery and resource decoding. That coverage includes an object/CAS set, a venue lot, and a world archive; it does not establish their gameplay behavior. Sanitized input/output digests and counts are recorded in [collection results](docs/collection-results.json).

Run the synthetic regression suite without game assets:

```sh
python -m unittest discover -s tests -v
```

## Format references

- [Sims3Pack format documentation](https://modthesims.info/wiki.php?title=Sims_3:SIMS3PACK): archive metadata and member layout.
- [Pinned uyjulian reference implementation](https://gist.github.com/uyjulian/bd24b98a4c97b775c9ab/9f155828f41911cbc8eff4fc4021b45d49dd10c1): prior work on recovering legacy package headers. This project's checksum transform is independently implemented.
