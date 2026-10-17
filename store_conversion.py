"""Bounded object Store-content conversion with explicit resource-key ownership."""
import copy
import json
import re
import struct
import xml.etree.ElementTree as ET

from sims3pack_formats import (RecoveryError, MIB, crc, digest, parse_archive,
                              parse_index, package_index, read_archive, refpack,
                              write_outputs)


def need(condition, message):
    if not condition:
        raise RecoveryError(message)


def u32(data, offset):
    need(0 <= offset <= len(data) - 4, 'Truncated DWORD')
    return struct.unpack_from('<I', data, offset)[0]


RCOL = {0x736884f1, 0x03b4c61d, 0x01d10f34, 0xd382bf57, 0x01661233}


CHUNK_TAGS = {
    0x01d10f34: b'MLOD', 0x01d0e75d: b'MATD',
    0x0229684b: b'VBUF', 0x0229684f: b'IBUF',
    0x01d0e76b: b'SKIN', 0x02019972: b'MTST',
    0x01d0e723: b'VRTF', 0x01d0e6fb: b'VBUF',
    0x01d0e70f: b'IBUF', 0x736884f1: b'VPXY',
    0x01661233: b'MODL', 0x03b4c61d: b'LITE', 0xd382bf57: b'FTPT',
}


LEAF_TYPES = {0x220557da, 0x00b2d882, 0x0166038c, 0x0580a2b5,
              0xf3a38370, 0x082d0369, 0x073faa07, 0x08646069}

# Thumbnail lookup uses these original TGIs; they are not object identities.
THUMBNAIL_TYPES = {0x0580a2b4, 0x0580a2b5, 0x0580a2b6,
                   0x626f60cc, 0x626f60cd, 0x626f60ce,
                   0x0580a2cd, 0x0580a2ce, 0xd84e7fc5, 0xd84e7fc6}


class ResourceKeyMap:
    """Own the exact source identities and every permitted remapping."""
    def __init__(self, keys):
        self.keys = set(keys)
        self.changes = {}
        self.external = set()
        self.shared = []
        destinations = {}
        for key in sorted(self.keys):
            ty, group, instance = key
            destination = key if ty in THUMBNAIL_TYPES else (ty, norm_group(group), instance)
            need(destination not in destinations,
                 f'Resource identity collision after conversion: {key_text(destination)}')
            destinations[destination] = key
            if key != destination:
                self.changes[key] = destination

    @classmethod
    def from_packages(cls, packages):
        origins = {}
        for name, resources in packages.items():
            for key, payload in resources:
                # These are package-local metadata, deliberately not merged into
                # the importer's global resource namespace.
                if key == (0x73e93eeb, 0, 0):
                    continue
                fingerprint = digest(payload)
                if key in origins:
                    previous, names = origins[key]
                    need(fingerprint == previous,
                         f'Conflicting shared resource {key_text(key)} in {names[0]} and {name}')
                    names.append(name)
                else:
                    origins[key] = fingerprint, [name]
        result = cls(key for resources in packages.values() for key, _ in resources)
        result.shared = [{'key': key_text(key), 'expanded_sha256': fingerprint,
                          'packages': names} for key, (fingerprint, names) in sorted(origins.items()) if len(names) > 1]
        return result

    def resolve(self, key, context):
        if key in self.changes:
            return self.changes[key]
        if key not in self.keys:
            need(key[0] in THUMBNAIL_TYPES or norm_group(key[1]) == key[1],
                 f'Unknown external Store resource mapping in {context}: {key_text(key)}')
            self.external.add(key)
        return key


def norm_group(value):
    return value & 0xfeffffff if value & 0xff000000 in (0x01000000, 0x03000000) else value


def key_text(key):
    ty, gr, inst = key
    return f'{ty:08x}:{gr:08x}:{inst:016x}'


def tgi_bytes(key, order):
    ty, gr, inst = key
    if order == 'TGI':
        return struct.pack('<IIQ', ty, gr, inst)
    if order == 'ITG':
        return struct.pack('<QII', inst, ty, gr)
    if order == 'IGT':
        return struct.pack('<QII', inst, gr, ty)
    raise RecoveryError('Unsupported TGI order')


class Editor:
    def __init__(self, data, key_map):
        self.key_map = key_map
        self.before = bytes(data)
        self.data = bytearray(data)
        self.references = []
        self.patches = {}

    def tgi(self, offset, order, context):
        need(0 <= offset <= len(self.data) - 16, f'Truncated {context} reference')
        if order == 'TGI':
            ty, gr, inst = struct.unpack_from('<IIQ', self.data, offset)
            group_offset = offset + 4
        elif order == 'ITG':
            inst, ty, gr = struct.unpack_from('<QII', self.data, offset)
            group_offset = offset + 12
        elif order == 'IGT':
            inst, gr, ty = struct.unpack_from('<QII', self.data, offset)
            group_offset = offset + 8
        else:
            raise RecoveryError('Unsupported TGI order')
        old = (ty, gr, inst)
        new = self.key_map.resolve(old, context)
        if old != new:
            need(group_offset not in self.patches, 'Overlapping reference interpretation')
            struct.pack_into('<I', self.data, group_offset, new[1])
            self.patches[group_offset] = (gr, new[1])
            self.references.append({'context': context, 'offset': offset,
                                    'order': order, 'before': key_text(old),
                                    'after': key_text(new)})
            need(bytes(self.data[offset:offset + 16]) == tgi_bytes(new, order),
                 'Reference rewrite verification failed')
        return new

    def finish(self):
        proof = bytearray(self.before)
        for offset, (old, new) in self.patches.items():
            need(u32(proof, offset) == old, 'Unexpected original reference group')
            struct.pack_into('<I', proof, offset, new)
        need(proof == self.data, 'Unexpected payload mutation')
        return bytes(self.data)


def sized_tgi_list(editor, base, limit, offset_field, size_field, context):
    data = editor.data
    relative = u32(data, base + offset_field)
    size = u32(data, base + size_field)
    position = base + offset_field + 4 + relative
    need(base + size_field + 4 <= position <= limit - 4, f'Bad {context} list position')
    count = u32(data, position)
    need(size == 4 + count * 16, f'Bad {context} list length')
    need(position + size == limit, f'Unexamined {context} list tail')
    for i in range(count):
        editor.tgi(position + 4 + 16 * i, 'TGI', context)


def rcol(editor):
    data = editor.data
    need(len(data) >= 20, 'Truncated RCOL')
    version, public, unused, external, chunks = struct.unpack_from('<IIIII', data)
    need(version == 3 and unused == 0 and 0 < public <= chunks, 'Unsupported RCOL header')
    end_header = 20 + 16 * (external + chunks) + 8 * chunks
    need(end_header <= len(data), 'RCOL tables exceed resource')
    chunk_keys = []
    for i in range(chunks + external):
        key = editor.tgi(20 + 16 * i, 'ITG',
                         'RCOL chunk identity' if i < chunks else 'RCOL external resource')
        if i < chunks:
            chunk_keys.append(key)
    chunk_index = 20 + 16 * (external + chunks)
    intervals = []
    swizzles = {}
    vertex_links = {}
    for i in range(chunks):
        position = u32(data, chunk_index + 8 * i)
        size = u32(data, chunk_index + 8 * i + 4)
        need(size >= 4 and end_header <= position <= len(data) - size, 'RCOL chunk out of bounds')
        intervals.append((position, position + size))
        ty = chunk_keys[i][0]
        tag = bytes(data[position:position + 4])
        if ty == 0:
            # The untagged blocks reached by VBUF.SwizzleInfo contain segment
            # sizes/counts/offsets and scalar swizzle commands, not TGIs.
            need(chunk_keys[i] == (0, 0, 0), 'Unexpected Swizzle Info identity')
            swizzles[i] = bytes(data[position:position + size])
        else:
            need(ty in CHUNK_TAGS and CHUNK_TAGS[ty] == tag,
                 f'Unsupported RCOL chunk type/tag: {ty:08x}/{tag!r}')
        if tag == b'VPXY':
            need(u32(data, position + 4) == 4, 'Unsupported VPXY version')
            sized_tgi_list(editor, position, position + size, 8, 12, 'VPXY inner resource')
        if tag == b'VBUF':
            need(size >= 16 and u32(data, position + 4) == 0x101, 'Unsupported VBUF header')
            reference = u32(data, position + 12)
            if reference:
                reference_type, ordinal = reference >> 28, reference & 0x0fffffff
                need(reference_type in (0, 1) and ordinal > 0,
                     'VBUF Swizzle Info does not reference a local RCOL chunk')
                target = ordinal - 1 + (public if reference_type == 1 else 0)
                need(0 <= target < chunks, 'VBUF Swizzle Info chunk is out of bounds')
                need((target < public) == (reference_type == 0), 'Bad Swizzle Info reference visibility')
                vertex_links.setdefault(target, []).append(size - 16)
    previous = end_header
    for start, stop in sorted(intervals):
        need(start >= previous, 'Overlapping RCOL chunks')
        need(not any(data[previous:start]), 'Unexamined nonzero RCOL gap')
        previous = stop
    need(not any(data[previous:]), 'Unexamined nonzero RCOL tail')
    need(set(vertex_links) == set(swizzles), 'Unexpected or unreferenced Swizzle Info chunk')
    for ordinal, payload in swizzles.items():
        validate_swizzle(payload, vertex_links[ordinal])


def validate_swizzle(data, buffer_lengths):
    # Format authors' original specification, corroborated by VBUF.cs and its
    # ChunkReference parser in pinned s3pi:
    # https://modthesims.info/wiki.php?title=Sims_3%3A0x01D0E6FB
    segments = u32(data, 0)
    need(0 < segments <= 256 and buffer_lengths, 'Unsupported Swizzle Info segment count')
    cursor = 4
    intervals = []
    for _ in range(segments):
        vertex_size = u32(data, cursor)
        count = u32(data, cursor + 4)
        offset = u32(data, cursor + 8)
        cursor += 12
        need(0 < vertex_size <= 256 and vertex_size % 4 == 0 and count > 0,
             'Bad Swizzle Info vertex size/count')
        need(cursor + vertex_size <= len(data), 'Truncated Swizzle Info commands')
        commands = struct.unpack_from('<' + 'I' * (vertex_size // 4), data, cursor)
        need(all(command in (0, 1, 2) for command in commands), 'Unsupported swizzle command')
        cursor += vertex_size
        end = offset + vertex_size * count
        need(all(end <= bound for bound in buffer_lengths), 'Swizzle segment exceeds its VBUF data')
        intervals.append((offset, end))
    need(cursor == len(data), 'Unexamined Swizzle Info tail')
    previous = 0
    for start, stop in sorted(intervals):
        need(start >= previous, 'Overlapping Swizzle Info segments')
        previous = stop


def manifest(data, key_map):
    need(len(data) <= 1024 * 1024 and b'<!DOCTYPE' not in data.upper(), 'Unsupported manifest size/DTD')
    original = ET.fromstring(data.rstrip(b'\0'))
    need(original.tag == 'manifest' and original.get('version') == '3'
         and original.get('packagetype') == 'object', 'Unsupported manifest')
    root = copy.deepcopy(original)
    need(root.get('paidcontent') in ('true', 'false'), 'Unsupported manifest Store flag')
    flag_change = 'manifest paidcontent true -> false' if root.get('paidcontent') == 'true' else None
    root.set('paidcontent', 'false')
    edits = []
    for node in root.iter('reskey'):
        text = node.text or ''
        match = re.fullmatch(r'([0-9a-fA-F]+):([0-9a-fA-F]{8}):([0-9a-fA-F]{8}):([0-9a-fA-F]{1,16})', text)
        need(match is not None, f'Unsupported manifest reskey: {text}')
        prefix, ty, gr, inst = match.groups()
        need(prefix in ('0', '1'), 'Unsupported manifest resource membership flag')
        before = (int(ty, 16), int(gr, 16), int(inst, 16))
        if prefix == '1':
            need(before in key_map.keys, f'Manifest included resource is absent: {key_text(before)}')
        after = key_map.resolve(before, 'manifest reskey')
        if before != after:
            node.text = f'{prefix}:{ty}:{after[1]:08x}:{inst}'
            edits.append({'context': 'manifest reskey', 'before': key_text(before),
                          'after': key_text(after)})
    output = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    need(ET.tostring(ET.fromstring(output)) == ET.tostring(root), 'Manifest roundtrip mismatch')
    return (output if edits or flag_change else data), edits, flag_change


def validate_leaf(ty, data, expected_names):
    # Unsupported leaf formats fail. Known image/string/hash/script formats
    # retain their payload bytes and instance IDs.
    if ty == 0x00b2d882:
        need(len(data) >= 128 and data.startswith(b'DDS ') and u32(data, 4) == 124
             and u32(data, 12) > 0 and u32(data, 16) > 0, 'Unexpected DDS texture header')
    elif ty == 0x0580a2b5:
        need(data.startswith(b'\x89PNG\r\n\x1a\n'), 'Unexpected thumbnail format')
    elif ty == 0x220557da:
        need(len(data) >= 17 and data[:7] == b'STBL\x02\0\0', 'Unexpected string table format')
        count = u32(data, 7)
        need(data[11:17] == bytes(6), 'Unsupported string table reserved fields')
        need(count <= (len(data) - 17) // 12, 'String table count exceeds resource')
        position, keys = 17, set()
        for _ in range(count):
            need(position + 12 <= len(data), 'Truncated string table entry')
            key, length = struct.unpack_from('<QI', data, position)
            need(key not in keys, 'Duplicate string table key')
            keys.add(key)
            position += 12
            need(position + 2 * length <= len(data), 'Truncated string table text')
            data[position:position + 2 * length].decode('utf-16-le')
            position += 2 * length
        need(position == len(data), 'Unexamined string table tail')
    elif ty == 0xf3a38370:
        need(len(data) == 24 and u32(data, 0) == 1 and u32(data, 4) == 1,
             'Unexpected NGMP hash map format')
    elif ty == 0x0166038c:
        need(u32(data, 0) == 1, 'Unsupported name map')
        pos = 8
        count = u32(data, 4)
        need(count <= (len(data) - 8) // 12, 'Name-map count exceeds resource')
        for _ in range(count):
            length = u32(data, pos + 8)
            need(pos + 12 + length <= len(data), 'Truncated name map string')
            data[pos + 12:pos + 12 + length].decode('utf-8')
            pos += 12 + length
        need(pos == len(data), 'Unexpected name map tail')
    elif ty == 0x082d0369:
        count = u32(data, 2)
        need(len(data) == 6 + 16 * count and data[:2] == b'\x01\0'
             and count == len(expected_names),
             'Unsupported package GUID list')
        ids = {f'0x{int.from_bytes(data[6 + 16*i:22 + 16*i], "little"):032x}.package' for i in range(count)}
        need(ids == expected_names, 'Unexpected package GUID identities')
    elif ty == 0x08646069:
        need(data == b'\0\0)\0Sims3.Store.Objects.ChewToyHourglassIams',
             'Unexpected script class metadata')
    elif ty == 0x073faa07:
        need(len(data) >= 71 and data[0] == 1 and u32(data, 1) == 0x2bc4f79f,
             'Unsupported signed assembly envelope')
        count = struct.unpack_from('<H', data, 69)[0]
        need(count > 0 and len(data) == 71 + count * 520, 'Truncated or empty signed assembly envelope')
    else:
        raise RecoveryError(f'Unsupported leaf type {ty:08x}')


def convert_payload(ty, data, key_map, expected_names):
    if ty == 0x73e93eeb:
        return manifest(data, key_map)
    editor = Editor(data, key_map)
    if ty in RCOL:
        rcol(editor)
    elif ty in (0x319e4f1d, 0x02dc343f):
        need(u32(data, 0) == (0x16 if ty == 0x319e4f1d else 7),
             'Unsupported object catalog/key version')
        sized_tgi_list(editor, 0, len(data), 4, 8, 'OBJD' if ty == 0x319e4f1d else 'OBJK')
    elif ty == 0x033a1435:
        need(u32(data, 0) == 8, 'Unsupported texture compositor version')
        position = 8 + u32(data, 4)
        need(8 <= position < len(data), 'Bad texture compositor list offset')
        count = data[position]
        need(position + 1 + count * 16 == len(data), 'Bad texture compositor list extent')
        for i in range(count):
            editor.tgi(position + 1 + i * 16, 'IGT', 'texture compositor')
    elif ty in LEAF_TYPES:
        validate_leaf(ty, data, expected_names)
    else:
        raise RecoveryError(f'Unsupported potentially reference-bearing type {ty:08x}')
    return editor.finish(), editor.references, None


def write_package(resources):
    need(len({key for key, _ in resources}) == len(resources), 'Duplicate package resource identity')
    total = 96 + sum(len(data) for _, data in resources) + 4 + 32 * len(resources)
    need(total <= 0xffffffff and all(len(data) <= 0x7fffffff for _, data in resources),
         'Converted package exceeds DBPF offset/size fields')
    body = bytearray(96)
    body[:4] = b'DBPF'
    index = bytearray(struct.pack('<I', 0))
    for (ty, gr, inst), data in resources:
        offset = len(body)
        body.extend(data)
        index.extend(struct.pack('<IIIIIIIHH', ty, gr, inst >> 32, inst & 0xffffffff,
                                 offset, len(data) | 0x80000000, len(data), 0, 1))
    offset = len(body)
    for pos, value in ((4, 2), (36, len(resources)), (44, len(index)), (60, 3), (64, offset)):
        struct.pack_into('<I', body, pos, value)
    body.extend(index)
    rows = parse_index(body, offset, len(resources), 32)
    need(len(rows) == len(resources), 'Written index count mismatch')
    for row, (key, expected) in zip(rows, resources):
        need((int(row['type'], 16), int(row['group'], 16), int(row['instance'], 16)) == key,
             'Written resource identity mismatch')
        need(bytes(body[row['offset']:row['offset'] + row['stored_bytes']]) == expected,
             'Written resource payload mismatch')
    return bytes(body)


def expand_package(data, max_resource_bytes, max_expanded_bytes):
    fixed, rows, repaired = package_index(data)
    known = RCOL | LEAF_TYPES | {0x73e93eeb, 0x319e4f1d, 0x02dc343f, 0x033a1435}
    need(all(int(row['type'], 16) in known for row in rows),
         'Unsupported resource type; public conversion currently supports the validated object layouts only')
    total = sum(row['expanded_bytes'] for row in rows)
    need(total <= max_expanded_bytes and all(row['expanded_bytes'] <= max_resource_bytes for row in rows),
         'Package resources exceed the selected expansion limits; no expansion attempted')
    resources = []
    for row in rows:
        raw = memoryview(fixed)[row['offset']:row['offset'] + row['stored_bytes']]
        decoded = refpack(raw, row['expanded_bytes']) if row['compression'] else bytes(raw)
        key = int(row['type'], 16), int(row['group'], 16), int(row['instance'], 16)
        resources.append((key, decoded))
    need(len({key for key, _ in resources}) == len(resources), 'Duplicate input package resource identity')
    return resources, total, repaired


def rebuild_archive(data, xml, members, payloads):
    """Update only member checksums/lengths/offsets; preserve archive identities."""
    root = ET.fromstring(xml)
    nodes = root.findall('PackagedFile')
    need(len(nodes) == len(members) == len(payloads), 'Archive rebuild member count mismatch')
    offset = 0
    for node, member, payload in zip(nodes, members, payloads):
        for field in ('Name', 'Offset', 'Length', 'Crc'):
            need(len(node.findall(field)) == 1, f'Ambiguous archive member {field}')
        need(node.findtext('Name') == member['name'], 'Archive member identity changed')
        node.find('Offset').text = str(offset)
        node.find('Length').text = str(len(payload))
        node.find('Crc').text = crc(payload)
        offset += len(payload)
    xml_result = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    need(len(xml_result) <= 0xffffffff, 'Archive XML exceeds its size field')
    rebuilt = data[:13] + struct.pack('<I', len(xml_result)) + xml_result + b''.join(payloads)
    _, _, checked, _ = parse_archive(rebuilt)
    for original, current, payload in zip(members, checked, payloads):
        need(original['name'] == current['name'] and len(payload) == current['bytes']
             and crc(payload) == current['crc'], 'Rebuilt archive verification failed')
    return rebuilt


def convert_archive(path, output, max_input_bytes=256 * MIB,
                    max_resource_bytes=128 * MIB, max_expanded_bytes=1024 * MIB):
    data, xml, members, metadata = read_archive(path, max_input_bytes)
    need(metadata['type'] == 'object', 'World archives are not supported by public conversion')
    loaded, expanded = {}, 0
    for member in members:
        start, size = member['offset'], member['bytes']
        payload = data[start:start + size]
        if member['magic'] != 'DBPP':
            need(crc(payload) == member['crc'], f'Unprotected member CRC mismatch: {member["name"]}')
        if member['magic'] in ('DBPP', 'DBPF'):
            need(member['content_type'] == 'object' and member['name'].lower().endswith('.package'),
                 'CAS parts, lots and other member types are not supported by public conversion')
            resources, used, repaired = expand_package(payload, max_resource_bytes, max_expanded_bytes - expanded)
            expanded += used
            loaded[member['name']] = resources, repaired
        else:
            need(member['name'].lower().endswith('.png') and payload.startswith(b'\x89PNG\r\n\x1a\n'),
                 f'Unsupported non-package archive member: {member["name"]}')
    need(loaded, 'No object packages were found')
    key_map = ResourceKeyMap.from_packages({name: rows for name, (rows, _) in loaded.items()})
    need(key_map.changes, 'No supported Store resource groups require conversion')
    names = set(loaded)
    payloads, files, packages = [], [], []
    for member in members:
        source = data[member['offset']:member['offset'] + member['bytes']]
        if member['name'] not in loaded:
            payloads.append(source)
            continue
        resources, repaired = loaded[member['name']]
        converted, items = [], []
        for key, before in resources:
            after, references, flag_change = convert_payload(key[0], before, key_map, names)
            destination = key_map.resolve(key, 'resource index')
            if not references and not flag_change:
                need(before == after, 'Unrelated payload changed')
            converted.append((destination, after))
            items.append({'before_key': key_text(key), 'after_key': key_text(destination),
                          'before_expanded_sha256': digest(before), 'after_expanded_sha256': digest(after),
                          'payload_unchanged': before == after, 'references': references,
                          'manifest_flag_change': flag_change})
        rebuilt = write_package(converted)
        payloads.append(rebuilt)
        files.append((output / 'Packages' / path.stem / member['name'], rebuilt))
        packages.append({'name': member['name'], 'header_recovered': repaired,
                         'source_sha256': digest(source), 'output_sha256': digest(rebuilt),
                         'resources': items})
    archive = rebuild_archive(data, xml, members, payloads)
    destination = output / (path.stem + '.converted.Sims3Pack')
    rows = [row for package in packages for row in package['resources']]
    report = {'file': str(path), 'source_sha256': digest(data), **metadata,
              'output': str(destination), 'output_sha256': digest(archive),
              'converted_packages': len(packages), 'validated_resources': len(rows),
              'expanded_resource_bytes': expanded,
              'index_group_changes': sum(row['before_key'] != row['after_key'] for row in rows),
              'reference_changes': sum(len(row['references']) for row in rows),
              'unchanged_payloads': sum(row['payload_unchanged'] for row in rows),
              'instance_ids_changed': 0,
              'thumbnail_keys_preserved': [key_text(key) for key in sorted(key_map.keys) if key[0] in THUMBNAIL_TYPES],
              'resource_key_map': [{'before': key_text(old), 'after': key_text(new)} for old, new in sorted(key_map.changes.items())],
              'preserved_external_resource_keys': [key_text(key) for key in sorted(key_map.external)],
              'identical_shared_resources': key_map.shared,
              'packages': packages,
              'runtime_verification': 'Not performed by this utility; verify catalog, persistence and interactions separately.',
              'limitations': ['Validated signed assembly envelopes are preserved; script behavior is unverified.',
                              'Unsupported resource/member formats or unknown external Store mappings fail without output.']}
    files.extend([(destination, archive),
                  (output / (path.stem + '.conversion-report.json'), (json.dumps(report, indent=2) + '\n').encode())])
    need(all(target.resolve() != path.resolve() for target, _ in files), 'An output would replace the source archive')
    write_outputs(files)
    return {key: value for key, value in report.items() if key != 'packages'}
