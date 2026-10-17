# Contributing

Keep reports reproducible and distinguish archive parsing, resource decoding,
launcher installation, catalog visibility, and gameplay testing. Passing one
stage does not imply that the next passed. Include your game version, content
name, input digest, command, and the smallest useful error or result.

Do not upload game assets, saves, entitlement/account files, executable files,
or raw investigation exports. Generated reports contain local paths and must
be reviewed before sharing. Use synthetic fixtures for regression tests.

Preserve original inputs and instance IDs. Resource group changes must use an
explicit map shared by indexes and parsed references; preserve distinct thumbnail
identities and reject ambiguous mappings. New format support should
reject ambiguous structures and check expansion limits before allocation.
Document both demonstrated support and unavailable runtime verification.

Run `python -m unittest discover -s tests -v` before submitting changes.
