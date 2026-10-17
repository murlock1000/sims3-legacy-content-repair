"""Synthetic format fixtures only; no game data or external dependencies."""
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

import sims3pack_repair as repair


def package(shared=0, compressed=False):
    payload = b'ABCD'
    if compressed:
        # Four literals, then an overlapping copy of 100 bytes at distance four.
        payload = b'\x10\xfb\x00\x00\x68\xe0ABCD\xc0\x00\x03\x5f\xfc'
    index = bytearray(struct.pack('<I', shared))
    identifiers = [0x12345678, 7, 0x1234]
    for bit, value in zip((1, 2, 4), identifiers):
        if shared & bit:
            index.extend(struct.pack('<I', value))
    for bit, value in zip((1, 2, 4), identifiers):
        if not shared & bit:
            index.extend(struct.pack('<I', value))
    index.extend(struct.pack('<IIIIHH', 9, 96, len(payload) | 0x80000000,
                             104 if compressed else len(payload),
                             0xffff if compressed else 0, 1))
    return b'DBPP' + bytes(range(4, 96)) + payload + index


def archive(members, kind='object'):
    root = ET.Element('Sims3Package', Type=kind)
    ET.SubElement(root, 'ArchiveVersion').text = '1.5'
    offset = 0
    body = bytearray()
    for name, content_type, data in members:
        member = ET.SubElement(root, 'PackagedFile')
        for key, value in [('Name', name), ('ContentType', content_type),
                           ('Offset', offset), ('Length', len(data)),
                           ('Crc', repair.crc(data))]:
            ET.SubElement(member, key).text = str(value)
        body.extend(data)
        offset += len(data)
    xml = ET.tostring(root)
    return struct.pack('<I', 7) + b'TS3Pack\x01\x05' + struct.pack('<I', len(xml)) + xml + body


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'synthetic.Sims3Pack'
        self.output = self.root / 'out'

    def source_archive(self, members=None, kind='object'):
        data = archive(members or [('item.package', 'object', package())], kind)
        self.source.write_bytes(data)
        return data

    def test_header_recovery_preserves_payload_and_ids_for_all_index_layouts(self):
        for flags in range(8):
            with self.subTest(flags=flags):
                original = package(flags)
                fixed, rows = repair.recover_header(original)
                self.assertEqual(fixed[:8], b'DBPF\x02\x00\x00\x00')
                self.assertEqual(fixed[96:], original[96:])
                self.assertEqual(rows[0]['instance'], '0000123400000009')
                self.assertEqual(rows[0]['type'], '12345678')
                offset = struct.unpack_from('<I', fixed, 64)[0]
                self.assertEqual(repair.parse_index(fixed, offset, 1, 32 - 4 * flags.bit_count()), rows)

    def test_overlapping_refpack_copy_and_resource_hashes(self):
        fixed, rows = repair.recover_header(package(compressed=True))
        self.assertEqual(repair.validate_resources(fixed, rows, 104, 104), 104)
        self.assertEqual(rows[0]['expanded_sha256'], repair.digest(b'ABCD' * 26))

    def test_refpack_rejects_truncation_invalid_distance_and_overflow(self):
        samples = [(b'\x90\xfb\x00\x00\x00\x01', 1),
                   (b'\x10\xfb\x00\x00\x03\x00\x00', 3),
                   (b'\x10\xfb\x00\x00\x01\xe0ABCD', 1),
                   (b'\x10\xfb\x00\x00\x04\xe0AB', 4)]
        for encoded, expected in samples:
            with self.subTest(encoded=encoded), self.assertRaises(ValueError):
                repair.refpack(encoded, expected)

    def test_complete_archive_preservation_checksums_and_idempotence(self):
        original = self.source_archive([('item.package', 'object', package()),
                                        ('image.png', 'png', b'synthetic thumbnail')])
        report = repair.recover(self.source, self.output)
        self.assertEqual(report['validated_resources'], 1)
        self.assertEqual(self.source.read_bytes(), original)
        output = Path(report['output'])
        fixed, _, members, _ = repair.read_archive(output, repair.MIB)
        for member in members:
            start = member['offset']
            self.assertEqual(repair.crc(fixed[start:start + member['bytes']]), member['crc'])
        self.assertEqual(repair.recover(self.source, self.output), report)

    def test_object_and_cas_export_but_lots_and_worlds_do_not(self):
        members = [('object.package', 'object', package()),
                   ('cas.package', 'CASpart', package()),
                   ('venue.package', 'lot', package())]
        self.source_archive(members)
        repair.recover(self.source, self.output)
        exported = {p.name for p in (self.output / 'Packages').rglob('*.package')}
        self.assertEqual(exported, {'object.package', 'cas.package'})
        self.source_archive(members, kind='world')
        repair.recover(self.source, self.root / 'world-output')
        self.assertFalse((self.root / 'world-output' / 'Packages').exists())

    def test_unsafe_or_duplicate_member_names_rejected(self):
        for name in ('../bad.package', 'C:bad.package', 'bad\\name.package'):
            self.source_archive([(name, 'object', package())])
            with self.assertRaises(repair.RecoveryError):
                repair.audit(self.source)
        self.source_archive([('item.package', 'object', package()),
                             ('ITEM.package', 'object', package())])
        with self.assertRaises(repair.RecoveryError):
            repair.audit(self.source)

    def test_gaps_trailing_bytes_and_bad_unprotected_crc_rejected(self):
        original = self.source_archive()
        self.source.write_bytes(original.replace(b'<Offset>0</Offset>', b'<Offset>1</Offset>'))
        with self.assertRaises(repair.RecoveryError):
            repair.audit(self.source)
        self.source.write_bytes(original + b'extra')
        with self.assertRaises(repair.RecoveryError):
            repair.audit(self.source)
        original = self.source_archive([('item.package', 'object', package()),
                                        ('image.png', 'png', b'thumbnail')])
        self.source.write_bytes(original[:-1] + b'!')
        with self.assertRaisesRegex(repair.RecoveryError, 'CRC mismatch'):
            repair.recover(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_limits_before_output(self):
        self.source_archive()
        for kwargs in ({'max_input_bytes': 10}, {'max_resource_bytes': 3},
                       {'max_expanded_bytes': 3}):
            with self.subTest(kwargs=kwargs), self.assertRaises(repair.RecoveryError):
                repair.recover(self.source, self.output, **kwargs)
        self.assertFalse(self.output.exists())

    def test_conflicting_destination_prevents_all_writes(self):
        self.source_archive()
        self.output.mkdir()
        conflict = self.output / 'synthetic.repaired.Sims3Pack'
        conflict.write_bytes(b'keep me')
        with self.assertRaises(repair.RecoveryError):
            repair.recover(self.source, self.output)
        self.assertEqual(conflict.read_bytes(), b'keep me')
        self.assertFalse((self.output / 'Packages').exists())

    def test_plain_archive_is_not_reported_as_recovered(self):
        fixed, _ = repair.recover_header(package())
        self.source_archive([('item.package', 'object', fixed)])
        with self.assertRaisesRegex(repair.RecoveryError, 'No protected'):
            repair.recover(self.source, self.output)

    def test_batch_cli_reports_mixed_results_and_nonzero_exit(self):
        self.source_archive()
        (self.root / 'invalid.Sims3Pack').write_bytes(b'invalid')
        result = subprocess.run([sys.executable, str(Path(repair.__file__)), 'recover',
                                 str(self.root), '--out', str(self.output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual({r['status'] for r in json.loads(result.stdout)}, {'ok', 'error'})


if __name__ == '__main__':
    unittest.main()
