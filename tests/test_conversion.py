"""Synthetic Store-reference graphs; no proprietary content fixtures."""
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib

from sims3pack_formats import RecoveryError, crc, package_index, parse_archive
import store_conversion as convert
from test_repair import archive

OBJECT = (0x319e4f1d, 0x01000000, 0x123)
OBJECT_KEY = (0x02dc343f, 0x01000000, 0x456)
THUMBNAIL = (0x0580a2b5, 0x01000000, 0x789)
MANIFEST = (0x73e93eeb, 0, 0)


def png():
    def chunk(tag, payload):
        return struct.pack('>I', len(payload)) + tag + payload + struct.pack('>I', zlib.crc32(tag + payload))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(5))) + chunk(b'IEND', b''))


def catalog(version, references):
    return (struct.pack('<IIII', version, 4, 4 + 16 * len(references), len(references))
            + b''.join(convert.tgi_bytes(key, 'TGI') for key in references))


def resources(external=None):
    root = ET.Element('manifest', version='3', packagetype='object', paidcontent='true')
    keys = ET.SubElement(root, 'keylist')
    for key in (OBJECT, OBJECT_KEY, THUMBNAIL):
        ET.SubElement(keys, 'reskey').text = '1:' + convert.key_text(key)
    return [(MANIFEST, ET.tostring(root)),
            (OBJECT, catalog(0x16, [OBJECT_KEY, THUMBNAIL] + ([external] if external else []))),
            (OBJECT_KEY, catalog(7, [])), (THUMBNAIL, png())]


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'sample.Sims3Pack'
        self.output = self.root / 'converted'

    def source_archive(self, rows=None, kind='object', member_kind='object', protected=False):
        data = convert.write_package(rows if rows is not None else resources())
        if protected:
            data = b'DBPP' + bytes(92) + data[96:]
        original = archive([('item.package', member_kind, data), ('cover.png', 'png', png())], kind)
        raw, xml, _, _ = parse_archive(original)
        root = ET.fromstring(xml)
        ET.SubElement(root, 'PackageId').text = 'synthetic-archive-identity'
        new_xml = ET.tostring(root)
        original = raw[:13] + struct.pack('<I', len(new_xml)) + new_xml + raw[17 + len(xml):]
        self.source.write_bytes(original)
        return original

    def test_exact_mapping_preserves_thumbnails_instances_and_unrelated_references(self):
        key_map = convert.ResourceKeyMap([OBJECT, OBJECT_KEY, THUMBNAIL])
        self.assertEqual(key_map.changes[OBJECT], (OBJECT[0], 0, OBJECT[2]))
        self.assertEqual(key_map.resolve(THUMBNAIL, 'thumbnail'), THUMBNAIL)
        external = (OBJECT[0], 0, 0x999)
        self.assertEqual(key_map.resolve(external, 'basegame'), external)
        self.assertIn(external, key_map.external)
        for order in ('TGI', 'ITG', 'IGT'):
            editor = convert.Editor(convert.tgi_bytes(OBJECT, order), key_map)
            editor.tgi(0, order, 'synthetic')
            self.assertEqual(editor.finish(), convert.tgi_bytes((OBJECT[0], 0, OBJECT[2]), order))
        with self.assertRaisesRegex(RecoveryError, 'Unknown external Store'):
            key_map.resolve((OBJECT[0], 0x01000000, 0x999), 'missing dependency')

    def test_introduced_key_collisions_and_conflicting_shared_payloads_fail(self):
        with self.assertRaisesRegex(RecoveryError, 'collision'):
            convert.ResourceKeyMap([OBJECT, (OBJECT[0], 0, OBJECT[2])])
        with self.assertRaisesRegex(RecoveryError, 'Conflicting shared resource'):
            convert.ResourceKeyMap.from_packages({'a': [(OBJECT, b'a')], 'b': [(OBJECT, b'b')]})
        key_map = convert.ResourceKeyMap.from_packages({
            'a': [(OBJECT, b'same'), (MANIFEST, b'manifest-a')],
            'b': [(OBJECT, b'same'), (MANIFEST, b'manifest-b')]})
        self.assertEqual(key_map.shared[0]['packages'], ['a', 'b'])

    def test_complete_archive_rebuild_preserves_identities_png_and_all_resources(self):
        original = self.source_archive(protected=True)
        report = convert.convert_archive(self.source, self.output)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(report['validated_resources'], 4)
        self.assertEqual(report['index_group_changes'], 2)
        self.assertEqual(report['instance_ids_changed'], 0)
        data, xml, members, _ = parse_archive(Path(report['output']).read_bytes())
        self.assertEqual(ET.fromstring(xml).findtext('PackageId'), 'synthetic-archive-identity')
        self.assertEqual([m['name'] for m in members], ['item.package', 'cover.png'])
        for member in members:
            payload = data[member['offset']:member['offset'] + member['bytes']]
            self.assertEqual(crc(payload), member['crc'])
            if member['name'] == 'cover.png':
                self.assertEqual(payload, png())
        package = (self.output / 'Packages/sample/item.package').read_bytes()
        fixed, rows, recovered = package_index(package)
        self.assertFalse(recovered)
        actual = {(int(r['type'], 16), int(r['group'], 16), int(r['instance'], 16)):
                  fixed[r['offset']:r['offset'] + r['stored_bytes']] for r in rows}
        self.assertIn(THUMBNAIL, actual)
        self.assertEqual(actual[THUMBNAIL], png())
        self.assertEqual(ET.fromstring(actual[MANIFEST]).get('paidcontent'), 'false')
        self.assertNotIn(OBJECT, actual)
        self.assertIn((OBJECT[0], 0, OBJECT[2]), actual)
        self.assertEqual(convert.convert_archive(self.source, self.output), report)

    def test_unknown_formats_and_unmapped_dependencies_fail_before_any_output(self):
        for rows in (resources(external=(OBJECT[0], 0x01000000, 0x999)),
                     resources() + [((0xdeadbeef, 0, 1), b'unknown')],
                     [(OBJECT, b'bad-reference-table')]):
            self.source_archive(rows)
            with self.subTest(rows=rows), self.assertRaises(RecoveryError):
                convert.convert_archive(self.source, self.output)
            self.assertFalse(self.output.exists())
        for kind, member_kind in [('world', 'object'), ('object', 'CASpart'), ('object', 'lot')]:
            self.source_archive(kind=kind, member_kind=member_kind)
            with self.assertRaises(RecoveryError):
                convert.convert_archive(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_structural_reference_boundaries_and_manifest_membership(self):
        key_map = convert.ResourceKeyMap([OBJECT])
        for malformed in (b'', bytes(15)):
            with self.assertRaises(RecoveryError):
                convert.Editor(malformed, key_map).tgi(0, 'TGI', 'truncated')
        for resource in (catalog(0x16, [OBJECT])[:-1], catalog(0x16, []) + b'junk'):
            with self.assertRaises(RecoveryError):
                convert.convert_payload(OBJECT[0], resource, key_map, set())
        text = b'<manifest version="3" packagetype="object" paidcontent="true"><reskey>1:319e4f1d:00000000:999</reskey></manifest>'
        with self.assertRaisesRegex(RecoveryError, 'included resource is absent'):
            convert.manifest(text, key_map)
        unchanged = b'<manifest version="3" packagetype="object" paidcontent="false" />'
        payload, references, flag_change = convert.manifest(unchanged, key_map)
        self.assertEqual(payload, unchanged)
        self.assertEqual(references, [])
        self.assertIsNone(flag_change)

    def test_expansion_limit_checked_before_malformed_compressed_payload(self):
        package = bytearray(convert.write_package([(OBJECT, b'bad')]))
        index = struct.unpack_from('<I', package, 64)[0]
        struct.pack_into('<I', package, index + 4 + 24, 100)
        struct.pack_into('<H', package, index + 4 + 28, 0xffff)
        original = archive([('item.package', 'object', bytes(package))])
        self.source.write_bytes(original)
        with self.assertRaisesRegex(RecoveryError, 'expansion limits'):
            convert.convert_archive(self.source, self.output, max_expanded_bytes=50)
        self.assertFalse(self.output.exists())

    def test_conflicting_output_is_preserved_and_prevents_partial_package_writes(self):
        self.source_archive()
        self.output.mkdir()
        target = self.output / 'sample.converted.Sims3Pack'
        target.write_bytes(b'keep original destination')
        with self.assertRaisesRegex(RecoveryError, 'different bytes'):
            convert.convert_archive(self.source, self.output)
        self.assertEqual(target.read_bytes(), b'keep original destination')
        self.assertFalse((self.output / 'Packages').exists())

    def test_swizzle_and_leaf_boundaries_are_explicit(self):
        valid = struct.pack('<IIIII', 1, 4, 8, 0, 1)
        convert.validate_swizzle(valid, [32])
        for data, bounds in [(valid[:-1], [32]), (valid + b'\0', [32]), (valid, [31]),
                             (struct.pack('<IIIII', 1, 4, 8, 0, 3), [32])]:
            with self.assertRaises(RecoveryError):
                convert.validate_swizzle(data, bounds)
        stbl = b'STBL\x02\0\0' + struct.pack('<I', 0) + bytes(6)
        convert.validate_leaf(0x220557da, stbl, set())
        for malformed in (stbl[:-1], stbl[:11] + b'\1' + stbl[12:]):
            with self.assertRaises(RecoveryError):
                convert.validate_leaf(0x220557da, malformed, set())
        with self.assertRaises(RecoveryError):
            convert.validate_leaf(0x073faa07, bytes(71), set())

    def test_cli_reports_supported_and_unsupported_inputs_separately(self):
        self.source_archive()
        (self.root / 'unknown.Sims3Pack').write_bytes(b'invalid')
        script = Path(__file__).resolve().parents[1] / 'sims3pack_repair.py'
        result = subprocess.run([sys.executable, str(script), 'convert', str(self.root),
                                 '--out', str(self.output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual({r['status'] for r in json.loads(result.stdout)}, {'ok', 'error'})


if __name__ == '__main__':
    unittest.main()
