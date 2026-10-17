#!/usr/bin/env python3
"""Inspect, recover, and convert locally supplied legacy Sims3Packs; Python 3.10+."""
import argparse
from pathlib import Path
import json
import re
import struct
import xml.etree.ElementTree as ET

from sims3pack_formats import (
    MIB, RecoveryError, audit, crc, digest, package_index, parse_index, read_archive,
    recover_header, refpack, validate_resources, write_outputs,
)


def recover(path, output, max_input_bytes=256 * MIB, max_resource_bytes=128 * MIB,
            max_expanded_bytes=1024 * MIB):
    data, xml_bytes, members, metadata = read_archive(path, max_input_bytes)
    if metadata['type'] not in ('object', 'world'):
        raise RecoveryError('Recovery currently supports object and world archives only')
    spans = list(re.finditer(rb'<Crc>([0-9a-fA-F]{16})</Crc>', xml_bytes))
    if len(spans) != len(members):
        raise RecoveryError('Unsupported CRC representation in archive XML')
    result, xml_result = bytearray(data), bytearray(xml_bytes)
    packages, files, changed, expanded = [], [], 0, 0
    for member, span in zip(members, spans):
        if span.group(1).decode().lower() != member['crc']:
            raise RecoveryError('Ambiguous XML CRC ordering')
        start, size = member['offset'], member['bytes']
        original = data[start:start + size]
        rows = None
        if member['magic'] in ('DBPP', 'DBPF'):
            recovered, rows, header_recovered = package_index(original)
            changed += header_recovered
        else:
            recovered = original
        if member['magic'] != 'DBPP' and crc(original) != member['crc']:
            raise RecoveryError(f'Unprotected member CRC mismatch: {member["name"]}')
        if rows is not None:
            expanded += validate_resources(recovered, rows, max_resource_bytes, max_expanded_bytes - expanded)
            packages.append({'name': member['name'], 'title': member['title'],
                             'content_type': member['content_type'], 'resource_count': len(rows),
                             'original_sha256': digest(original), 'recovered_sha256': digest(recovered),
                             'body_after_header_sha256': digest(original[96:]),
                             'header_recovered': member['magic'] == 'DBPP',
                             'resource_ids_and_stored_payloads_unchanged': True, 'resources': rows})
            # World and venue members require their archive's normal installation workflow.
            if (metadata['type'] == 'object' and member['content_type'] in ('object', 'CASpart')
                    and member['name'].lower().endswith('.package')):
                files.append((output / 'Packages' / path.stem / member['name'], recovered))
        result[start:start + size] = recovered
        xml_result[span.start(1):span.end(1)] = crc(recovered).encode('ascii')
    if not changed:
        raise RecoveryError('No protected DBPP headers require recovery')
    result[17:17 + len(xml_bytes)] = xml_result
    ET.fromstring(xml_result)
    destination = output / (path.stem + '.repaired.Sims3Pack')
    if destination.resolve() == path.resolve():
        raise RecoveryError('Output would replace the input archive')
    report = {'file': str(path), 'source_sha256': digest(data), **metadata,
              'output': str(destination), 'output_sha256': digest(result),
              'archive_size_unchanged': len(result) == len(data), 'recovered_headers': changed,
              'changed_regions': 'Recovered 96-byte package headers and their XML CRC values.',
              'validated_resources': sum(p['resource_count'] for p in packages),
              'expanded_resource_bytes': expanded, 'packages': packages,
              'runtime_verification': 'Not performed by this utility; validate installation and in-game content separately.'}
    files += [(destination, result),
              (output / (path.stem + '.report.json'), (json.dumps(report, indent=2) + '\n').encode())]
    if any(p.resolve() == path.resolve() for p, _ in files):
        raise RecoveryError('An output would replace the input file')
    write_outputs(files)
    return {key: value for key, value in report.items() if key != 'packages'}


def input_paths(values):
    paths = []
    for value in values:
        path = Path(value)
        candidates = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == '.sims3pack') if path.is_dir() else [path]
        paths.extend(candidates)
    unique = list(dict.fromkeys(p.resolve() for p in paths))
    if not unique:
        raise RecoveryError('No Sims3Pack files were selected')
    if len({p.stem.casefold() for p in unique}) != len(unique):
        raise RecoveryError('Selected archives have colliding output names; use separate output directories')
    return unique


def positive_mib(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('Memory limits must be positive MiB values')
    return number * MIB


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('audit', 'recover', 'convert'):
        p = sub.add_parser(command)
        p.add_argument('inputs', nargs='+', help='Files or a directory to inspect without recursion')
        p.add_argument('--max-input-mib', type=positive_mib, default=256 * MIB)
        if command == 'audit':
            p.add_argument('--report', type=Path)
        else:
            p.add_argument('--out', type=Path, required=True)
            p.add_argument('--max-resource-mib', type=positive_mib, default=128 * MIB)
            p.add_argument('--max-expanded-mib', type=positive_mib, default=1024 * MIB)
    args = parser.parse_args()
    try:
        paths = input_paths(args.inputs)
    except (OSError, RecoveryError) as error:
        parser.exit(1, f'error: {error}\n')
    results, failed = [], False
    for path in paths:
        try:
            if args.command == 'audit':
                result = audit(path, args.max_input_mib)
            else:
                if args.command == 'convert':
                    from store_conversion import convert_archive
                    operation = convert_archive
                else:
                    operation = recover
                result = operation(path, args.out, args.max_input_mib,
                                   args.max_resource_mib, args.max_expanded_mib)
            results.append({'status': 'ok', **result})
        except (OSError, ValueError, struct.error, ET.ParseError) as error:
            failed = True
            results.append({'file': str(path), 'status': 'error', 'reason': str(error)})
    encoded = (json.dumps(results, indent=2) + '\n').encode()
    if args.command == 'audit' and args.report:
        try:
            if args.report.resolve() in paths:
                raise RecoveryError('The report path would replace an input archive')
            write_outputs([(args.report, encoded)])
        except (OSError, RecoveryError) as error:
            parser.exit(1, f'error: {error}\n')
    print(encoded.decode(), end='')
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
