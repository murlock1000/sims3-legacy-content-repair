#!/usr/bin/env python3
"""Shared Sims3Pack/DBPF parsing, checksums and safe output ownership."""
from functools import reduce
import hashlib
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
    if width not in (20, 24, 28, 32) or offset < 96 or count < 0:
        raise RecoveryError('Invalid package index location/count/width')
    if offset + (36 - width) + count * width != len(data):
        raise RecoveryError('Package index extent does not match its count')
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
    return parse_archive(data)


def parse_archive(data):
    """Parse one complete archive byte sequence without filesystem effects."""
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


def package_index(data):
    """Own the protected/plain package header and index interpretation."""
    if data[:4] == b'DBPP':
        fixed, rows = recover_header(data)
        return fixed, rows, True
    if len(data) < 96 or data[:4] != b'DBPF' or struct.unpack_from('<II', data, 4) != (2, 0):
        raise RecoveryError('Unsupported plain DBPF header')
    count = struct.unpack_from('<I', data, 36)[0]
    offset = struct.unpack_from('<I', data, 64)[0]
    if offset < 96 or offset + 4 > len(data):
        raise RecoveryError('Invalid plain DBPF index position')
    flags = struct.unpack_from('<I', data, offset)[0]
    if flags & ~7:
        raise RecoveryError('Unsupported plain DBPF index flags')
    if struct.unpack_from('<I', data, 44)[0] != len(data) - offset:
        raise RecoveryError('Plain DBPF index size mismatch')
    return data, parse_index(data, offset, count, 32 - 4 * flags.bit_count()), False


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
