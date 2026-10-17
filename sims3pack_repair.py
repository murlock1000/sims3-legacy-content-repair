#!/usr/bin/env python3
"""Inspect and recover locally supplied legacy Sims3Packs; Python 3.10+."""
import argparse
from functools import reduce
from pathlib import Path
import hashlib
import json
import re
import struct
import xml.etree.ElementTree as ET

MASK = (1 << 64) - 1
MIB = 1024 * 1024
# The format's linear 8-bit CRC transform, represented by its eight basis vectors.
CRC_BASIS = (0x9336eaa9ebe1f042, 0x266dd453d7c3e185, 0xdfec420e45663349,
             0xbed9851c8acc6692, 0xef85e190ff783d66, 0xde0bc321fff17acc,
             0x2f216cea150205da, 0xcd74327dc0e5faf6)
CRC_TABLE = tuple(reduce(int.__xor__,
                  (value for bit, value in enumerate(CRC_BASIS) if byte & (1 << bit)), 0)
                  for byte in range(256))


class RecoveryError(ValueError):
    """An unsupported, ambiguous, malformed, or resource-limited input."""


def crc(data):
    value = MASK
    for byte in data:
        value = (value >> 8) ^ CRC_TABLE[(byte ^ value) & 255]
    return f'{(~int.from_bytes(value.to_bytes(8, "little"), "big")) & MASK:016x}'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def refpack(data, expected):
    if len(data) < 6 or data[0] & 0x3e != 0x10 or data[1] != 0xfb:
        raise ValueError('Invalid RefPack header')
    size_width = 4 if data[0] & 0x80 else 3
    cursor = 2 + size_width * (2 if data[0] & 1 else 1)
    if cursor >= len(data):
        raise ValueError('Truncated RefPack header')
    output = bytearray()

    def take(count):
        nonlocal cursor
        if cursor + count > len(data):
            raise ValueError('Truncated RefPack input')
        part = data[cursor:cursor + count]
        cursor += count
        return part

    while cursor < len(data) and len(output) < expected:
        control = take(1)[0]
        if control < 0x80:
            b1 = take(1)[0]
            literal, count, distance = control & 3, ((control & 0x1c) >> 2) + 3, ((control & 0x60) << 3) + b1 + 1
        elif control < 0xc0:
            b1, b2 = take(2)
            literal, count, distance = b1 >> 6, (control & 0x3f) + 4, ((b1 & 0x3f) << 8) + b2 + 1
        elif control < 0xe0:
            b1, b2, b3 = take(3)
            literal, count, distance = control & 3, ((control & 0x0c) << 6) + b3 + 5, ((control & 0x10) << 12) + (b1 << 8) + b2 + 1
        elif control < 0xfc:
            literal, count, distance = (control - 0xdf) * 4, 0, 0
        else:
            literal, count, distance = control - 0xfc, 0, 0
        if len(output) + literal + count > expected:
            raise ValueError('RefPack expansion exceeds the declared resource size')
        output.extend(take(literal))
        if count and distance > len(output):
            raise ValueError('Invalid RefPack back reference')
        for _ in range(count):
            output.append(output[-distance])
    if len(output) != expected or any(b != 0xfc for b in data[cursor:]):
        raise ValueError('Incomplete RefPack resource or unexpected trailing data')
    return bytes(output)


def parse_index(data, offset, count, width):
    flags = struct.unpack_from('<I', data, offset)[0]
    header_size = 36 - width
    if flags & ~7 or header_size != 4 + 4 * flags.bit_count():
        raise ValueError('Index flags do not match its entry width')
    cursor = offset + 4
    shared = {}
    for bit, key in [(1, 'type'), (2, 'group'), (4, 'hi')]:
        if flags & bit:
            shared[key] = struct.unpack_from('<I', data, cursor)[0]
            cursor += 4
    rows = []
    for _ in range(count):
        values = []
        for key in ['type', 'group', 'hi']:
            if key in shared:
                value = shared[key]
            else:
                value = struct.unpack_from('<I', data, cursor)[0]
                cursor += 4
            values.append(value)
        lo, position, size_word, expanded, compression, entry_flags = struct.unpack_from('<IIIIHH', data, cursor)
        cursor += 20
        stored = size_word & 0x7fffffff
        if not size_word & 0x80000000 or entry_flags != 1:
            raise ValueError('Invalid resource entry flags')
        if position < 96 or position + stored > offset:
            raise ValueError('Resource lies outside the package data region')
        if compression == 0:
            if stored != expanded:
                raise ValueError('Uncompressed resource size mismatch')
        elif compression == 0xffff:
            if stored > expanded:
                raise ValueError('Compressed size exceeds expanded size')
        else:
            raise ValueError(f'Unsupported compression: {compression:#x}')
        rows.append({'type': f'{values[0]:08x}', 'group': f'{values[1]:08x}',
                     'instance': f'{values[2]:08x}{lo:08x}', 'offset': position,
                     'stored_bytes': stored, 'expanded_bytes': expanded,
                     'compression': compression})
    if cursor != len(data):
        raise ValueError('Index does not exhaust the package')
    return rows


def recover_header(data):
    if data[:4] != b'DBPP' or len(data) < 96:
        raise ValueError('Expected this archive\'s protected package layout')
    candidates = []
    for width in [32, 28, 24, 20]:
        count = 0
        while len(data) - 4 - count * width >= 96 and struct.unpack_from('<I', data, len(data) - 4 - count * width)[0] in (0x10000, 0x1ffff):
            count += 1
        for candidate_count in range(1, count + 1):
            offset = len(data) - candidate_count * width - (36 - width)
            if offset < 96:
                continue
            try:
                rows = parse_index(data, offset, candidate_count, width)
            except (ValueError, struct.error):
                continue
            candidates.append((offset, candidate_count, width, rows))
    if len(candidates) != 1:
        raise ValueError(f'Index recovery is ambiguous: {len(candidates)} valid candidates')
    offset, count, width, rows = candidates[0]
    header = bytearray(96)
    header[:4] = b'DBPF'
    for position, value in [(4, 2), (36, count), (44, len(data) - offset), (60, 3), (64, offset)]:
        struct.pack_into('<I', header, position, value)
    recovered = bytes(header) + data[96:]
    assert recovered[96:] == data[96:]
    assert parse_index(recovered, offset, count, width) == rows
    return recovered, rows


def read_archive(path, max_input_bytes):
    if path.stat().st_size > max_input_bytes:
        raise RecoveryError('Input exceeds --max-input-mib; increase it explicitly for this file')
    with path.open('rb') as stream:
        data = stream.read(max_input_bytes + 1)
    if len(data) > max_input_bytes:
        raise RecoveryError('Input grew beyond --max-input-mib while reading')
    if len(data) < 17 or data[:11] != struct.pack('<I', 7) + b'TS3Pack':
        raise RecoveryError('Expected a TS3Pack archive')
    length = struct.unpack_from('<I', data, 13)[0]
    if 17 + length > len(data):
        raise RecoveryError('Truncated archive XML')
    xml_bytes = data[17:17 + length]
    try:
        xml = ET.fromstring(xml_bytes)
    except ET.ParseError as error:
        raise RecoveryError(f'Invalid archive XML: {error}') from error
    if xml.tag != 'Sims3Package':
        raise RecoveryError('Unsupported archive XML root')
    members, position, names = [], 0, set()
    for node in xml.findall('PackagedFile'):
        name = node.findtext('Name') or ''
        if not name or name.casefold() in names or name in ('.', '..') or any(c in name for c in '/\\:\x00'):
            raise RecoveryError('An archive member has an unsafe or duplicate filename')
        names.add(name.casefold())
        try:
            offset, size = int(node.findtext('Offset')), int(node.findtext('Length'))
            expected_crc = int(node.findtext('Crc'), 16)
            if not 0 <= expected_crc <= MASK:
                raise ValueError('CRC is outside its 64-bit range')
        except (ValueError, TypeError) as error:
            raise RecoveryError('Invalid member offset, length, or CRC') from error
        start = 17 + length + offset
        if offset != position or size < 0 or start + size > len(data):
            raise RecoveryError('Archive members overlap, contain gaps, or exceed the file')
        position = offset + size
        members.append({'name': name, 'offset': start, 'bytes': size,
                        'title': node.findtext('metatags/name'), 'crc': f'{expected_crc:016x}',
                        'content_type': node.findtext('ContentType'),
                        'magic': data[start:start + min(size, 4)].decode('ascii', errors='replace')})
    if not members or 17 + length + position != len(data):
        raise RecoveryError('Empty archive or unexamined trailing bytes')
    title = next((e.text for e in xml.findall('LocalizedNames/LocalizedName')
                  if e.get('Language') == 'en-US'), xml.findtext('DisplayName'))
    return data, xml_bytes, members, {'title': title, 'type': xml.get('Type'),
                                     'archive_version': xml.findtext('ArchiveVersion'),
                                     'minimum_version_metadata': xml.findtext('MinReqVersion')}


def audit(path, max_input_bytes=256 * MIB):
    data, _, members, metadata = read_archive(path, max_input_bytes)
    return {'file': str(path), 'sha256': digest(data), 'bytes': len(data), **metadata,
            'protected_packages': sum(m['magic'] == 'DBPP' for m in members), 'members': members}


def validate_resources(data, rows, max_resource_bytes, max_total_bytes):
    if any(row['expanded_bytes'] > max_resource_bytes for row in rows):
        raise RecoveryError('A resource exceeds --max-resource-mib; no expansion was attempted')
    total = sum(row['expanded_bytes'] for row in rows)
    if total > max_total_bytes:
        raise RecoveryError('Resources exceed --max-expanded-mib; no expansion was attempted')
    for row in rows:
        start = row['offset']
        raw = memoryview(data)[start:start + row['stored_bytes']]
        decoded = refpack(raw, row['expanded_bytes']) if row['compression'] else raw
        row['stored_sha256'] = digest(raw)
        row['expanded_sha256'] = digest(decoded)
    return total


def write_outputs(files):
    # Preflight every destination before creating any output; inputs are never edited.
    for path, data in files:
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_bytes() != data)):
            raise RecoveryError(f'Output already exists with different bytes: {path}')
    for path, data in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            continue
        with path.open('xb') as stream:
            stream.write(data)


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
        if member['magic'] == 'DBPP':
            recovered, rows = recover_header(original)
            changed += 1
        elif member['magic'] == 'DBPF':
            recovered = original
            if len(original) < 96 or struct.unpack_from('<II', original, 4) != (2, 0):
                raise RecoveryError('Unsupported plain DBPF header')
            count, offset = struct.unpack_from('<I', original, 36)[0], struct.unpack_from('<I', original, 64)[0]
            if offset < 96 or offset + 4 > len(original):
                raise RecoveryError('Invalid plain DBPF index position')
            flags = struct.unpack_from('<I', original, offset)[0]
            if flags & ~7:
                raise RecoveryError('Unsupported plain DBPF index flags')
            rows = parse_index(original, offset, count, 32 - 4 * flags.bit_count())
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
    for command in ('audit', 'recover'):
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
            result = audit(path, args.max_input_mib) if args.command == 'audit' else recover(
                path, args.out, args.max_input_mib, args.max_resource_mib, args.max_expanded_mib)
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
