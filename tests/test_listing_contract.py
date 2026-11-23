"""
T-1070g — listing schema v2 contract.

g-1: the model has no v1 fields; a DB trigger mirrors v2 → v1 for older
pods, and copies v1 → v2 when an older pod changed only v1; 0041 repairs
pre-trigger drift and unknown categories; the API keeps the deprecated names
as aliases; the gate (check_listing_contract) and the staged g-2 migrations.
"""
import importlib.util
import pathlib
import uuid
from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from apps.properties import listing_contract as lc
from apps.properties.models import Property
from apps.properties.taxonomy import categories_for_property_type, classify_type_text
from tests.factories import make_developer, make_property

URL = '/api/v1/properties/'
STAGED = pathlib.Path(__file__).resolve().parent.parent / 'apps/properties/contract_migrations'


def _v1(pk):
    with connection.cursor() as cur:
        cur.execute("SELECT area_marla, area_unit, area_sqm, property_type FROM properties WHERE id = %s", [pk])
        return cur.fetchone()


def _sql(sql, params=()):
    with connection.cursor() as cur:
        cur.execute(sql, params)


def _without_triggers(sql, params=()):
    """Write as if the trigger did not exist (pre-g-1 state)."""
    with connection.cursor() as cur:
        cur.execute("SET session_replication_role = replica")
        try:
            cur.execute(sql, params)
        finally:
            cur.execute("SET session_replication_role = DEFAULT")


def _staged(name):
    spec = importlib.util.spec_from_file_location(f'staged_{name}', STAGED / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ModelSurfaceTest(TestCase):
    def test_no_v1_fields_and_derived_type(self):
        names = {f.name for f in Property._meta.get_fields()}
        self.assertFalse(names & {'area_marla', 'area_unit', 'area_sqm', 'property_type'})
        self.assertNotIn('prop_fts_gin', {i.name for i in Property._meta.indexes})
        p = Property(category='hospitality')
        self.assertEqual(p.property_type, 'commercial')
        self.assertEqual(Property(category='land').property_type, 'plot')
        with self.assertRaises(AttributeError):
            p.property_type = 'plot'                     # writers must use category

    def test_size_m2_is_decimal_and_saved_instance_validates(self):   # KI-051
        _, org = make_developer()
        p = make_property(org=org, size_value=Decimal('1.5'), size_unit='kanal')
        self.assertIsInstance(p.size_m2, Decimal)
        self.assertEqual(p.size_m2, Decimal('758.7855'))
        p.full_clean()                                   # no reload needed any more

    def test_partial_save_recomputes_size_m2(self):
        _, org = make_developer()
        p = make_property(org=org)
        p.size_value = 10
        p.save(update_fields=['size_value'])
        self.assertEqual(Property.objects.get(pk=p.pk).size_m2, Decimal('252.9290'))


class MirrorTriggerTest(TestCase):
    def setUp(self):
        _, self.org = make_developer()

    def test_v2_writes_are_mirrored_to_v1(self):
        p = make_property(org=self.org, category='land', size_value=2, size_unit='kanal')
        self.assertEqual(_v1(p.pk), (Decimal('2.00'), 'kanal', Decimal('1011.7140'), 'plot'))
        Property.objects.filter(pk=p.pk).update(category='hospitality', size_value=3)   # bulk update
        self.assertEqual(_v1(p.pk)[0], Decimal('3.00'))
        self.assertEqual(_v1(p.pk)[3], 'commercial')

    def test_older_pod_changing_only_v1_wins(self):
        p = make_property(org=self.org, category='residential', subtype='house')
        _sql("UPDATE properties SET area_marla = 1, area_unit = 'kanal', area_sqm = 505.857 WHERE id = %s",
             [p.pk])
        p = Property.objects.get(pk=p.pk)
        self.assertEqual((p.size_value, p.size_unit, p.size_m2),
                         (Decimal('1.00'), 'kanal', Decimal('505.8570')))
        _sql("UPDATE properties SET property_type = 'plot' WHERE id = %s", [p.pk])
        p = Property.objects.get(pk=p.pk)
        self.assertEqual((p.category, p.subtype), ('land', None))

    def test_both_changed_v2_wins(self):
        p = make_property(org=self.org)
        _sql("UPDATE properties SET area_marla = 9, size_value = 7, property_type = 'industrial', "
             "category = 'commercial' WHERE id = %s", [p.pk])
        self.assertEqual(_v1(p.pk)[0], Decimal('7.00'))
        self.assertEqual(_v1(p.pk)[3], 'commercial')

    def test_older_pod_insert_with_v1_only(self):
        p = make_property(org=self.org)
        with connection.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'properties' ORDER BY ordinal_position")
            cols = [r[0] for r in cur.fetchall()]
        new_id = uuid.uuid4()
        override = {'id': '%s', 'ref_no': "'RT-TST-1'", 'size_value': 'NULL', 'size_unit': 'NULL',
                    'size_m2': 'NULL', 'category': 'NULL', 'subtype': 'NULL', 'search_tsv': 'NULL',
                    'area_marla': '4', 'area_unit': "'marla'", 'area_sqm': '101.1716',
                    'property_type': "'plot'"}
        select = ', '.join(override.get(c, c) for c in cols)
        _sql(f"INSERT INTO properties ({', '.join(cols)}) SELECT {select} FROM properties WHERE id = %s",
             [new_id, p.pk])
        q = Property.objects.get(pk=new_id)
        self.assertEqual((q.size_value, q.size_unit, q.category), (Decimal('4.00'), 'marla', 'land'))


class RepairAndGateTest(TestCase):
    def setUp(self):
        _, self.org = make_developer()

    def test_gate_passes_on_clean_db(self):
        make_property(org=self.org)
        self.assertEqual(lc.problems(), [])
        out = StringIO()
        call_command('check_listing_contract', stdout=out)
        self.assertIn('OK', out.getvalue())
        s = lc.status()
        self.assertEqual((s['trigger'], s['g1_applied'], s['v1_columns'], s['size_drift']),
                         (True, True, True, 0))

    def test_pre_trigger_drift_is_reported_then_repaired_v1_wins(self):
        a = make_property(org=self.org, location='A')
        b = make_property(org=self.org, location='B', category='residential', subtype='house')
        c = make_property(org=self.org, location='C')
        _without_triggers("UPDATE properties SET area_marla = 20, area_sqm = 505.858 WHERE id = %s", [a.pk])
        _without_triggers("UPDATE properties SET property_type = 'industrial' WHERE id = %s", [b.pk])
        _without_triggers("UPDATE properties SET category = 'flat', property_type = 'flat' WHERE id = %s",
                          [c.pk])
        found = lc.problems()
        self.assertEqual(len(found), 3, found)
        with self.assertRaises(CommandError):
            call_command('check_listing_contract', stdout=StringIO())
        with connection.cursor() as cur:
            self.assertEqual(lc.repair_drift(cur, batch_size=1), 3)
        a, b, c = (Property.objects.get(pk=x.pk) for x in (a, b, c))
        self.assertEqual(a.size_value, Decimal('20.00'))
        self.assertEqual((b.category, b.subtype), ('industrial', None))
        self.assertEqual((c.category, c.subtype, _v1(c.pk)[3]), ('residential', 'apartment', 'residential'))
        self.assertEqual(lc.problems(), [])

    def test_gate_blocks_without_trigger(self):
        _sql(lc.DROP_TRIGGER_SQL)
        self.assertTrue(any('trigger' in p for p in lc.problems()))


class StagedContractMigrationTest(TestCase):
    """The g-2 SQL, run inside the test transaction (DDL is transactional in Postgres)."""

    def test_staged_files_are_outside_the_loaded_migrations(self):
        from django.db.migrations.loader import MigrationLoader
        loaded = {n for a, n in MigrationLoader(connection).disk_migrations if a == 'properties'}
        self.assertIn('0041_repair_v1_drift', loaded)
        self.assertFalse({'0045_drop_listing_v1_columns', '0046_drop_prop_fts_gin'} & loaded)
        m45, m46 = _staged('0045_drop_listing_v1_columns'), _staged('0046_drop_prop_fts_gin')
        self.assertEqual(m45.Migration.dependencies, [('properties', '0044_geo_point_index')])
        self.assertEqual(m46.Migration.dependencies, [('properties', '0045_drop_listing_v1_columns')])
        self.assertFalse(m46.Migration.atomic)

    def test_gate_refuses_on_drift(self):
        m42 = _staged('0045_drop_listing_v1_columns')
        _, org = make_developer()
        p = make_property(org=org)
        _without_triggers("UPDATE properties SET area_marla = 99 WHERE id = %s", [p.pk])
        with self.assertRaises(RuntimeError):
            m42.gate(None, connection.schema_editor())

    def test_drop_then_restore(self):
        m42 = _staged('0045_drop_listing_v1_columns')
        _, org = make_developer()
        p = make_property(org=org, category='land', size_value=3)
        m42.gate(None, connection.schema_editor())
        _sql("SET CONSTRAINTS ALL IMMEDIATE")      # flush deferred FK checks before ALTER TABLE
        _sql(lc.DROP_TRIGGER_SQL)
        _sql(m42.DROP_COLUMNS_SQL)
        self.assertFalse(lc.status()['v1_columns'])
        self.assertEqual(lc.problems(), [])                       # nothing left to guard
        q = make_property(org=org, category='hospitality', size_value=8)  # g-1/g-2 code still works
        self.assertEqual(Property.objects.get(pk=q.pk).property_type, 'commercial')
        _sql("SET CONSTRAINTS ALL IMMEDIATE")
        _sql(m42.RESTORE_COLUMNS_SQL)
        _sql(lc.CREATE_TRIGGER_SQL)
        self.assertEqual(_v1(p.pk), (Decimal('3.00'), 'marla', Decimal('75.8787'), 'plot'))
        self.assertEqual(_v1(q.pk)[3], 'commercial')
        self.assertEqual(lc.problems(), [])


class ApiAliasTest(TestCase):
    def setUp(self):
        self.dev, self.org = make_developer()
        self.c = APIClient()
        self.c.force_authenticate(self.dev)

    def test_legacy_names_write_and_read(self):
        r = self.c.post(URL, {'title': 'P', 'city': 'Lahore', 'location': 'DHA', 'price': 1,
                              'property_type': 'plot', 'area_marla': '10', 'area_unit': 'marla'},
                        format='json')
        self.assertEqual(r.status_code, 201, r.data)
        p = Property.objects.get(title='P')
        self.assertEqual((p.category, p.size_value, p.size_unit), ('land', Decimal('10.00'), 'marla'))
        p.owner = self.dev
        p.save(update_fields=['owner'])
        d = self.c.get(f'{URL}{p.pk}/').data
        self.assertEqual((d['property_type'], d['area_marla'], d['area_unit'], d['area_sqm']),
                         ('plot', '10.00', 'marla', '252.9290'))
        r = self.c.patch(f'{URL}{p.pk}/', {'property_type': 'commercial'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(Property.objects.get(pk=p.pk).category, 'commercial')
        listed = self.c.get(URL).data
        rows = listed['results'] if isinstance(listed, dict) else listed
        self.assertEqual(rows[0]['property_type'], 'commercial')

    def test_conflicts_rejected(self):
        r = self.c.post(URL, {'title': 'P', 'city': 'Lahore', 'location': 'DHA', 'price': 1,
                              'property_type': 'plot', 'category': 'residential'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('property_type', r.data)
        r = self.c.post(URL, {'title': 'P', 'city': 'Lahore', 'location': 'DHA', 'price': 1,
                              'size_value': 5, 'area_marla': 6}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('area_marla', r.data)

    def test_hospitality_kept_by_commercial_alias_and_type_filter(self):
        h = make_property(org=self.org, owner=self.dev, title='hotel', category='hospitality',
                          subtype='hotel', listing_owner_type='organization')
        make_property(org=self.org, title='shop', category='commercial')
        make_property(org=self.org, title='house', category='residential')
        r = self.c.patch(f'{URL}{h.pk}/', {'property_type': 'commercial'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(Property.objects.get(pk=h.pk).category, 'hospitality')
        rows = self.c.get(URL + '?type=commercial').data
        rows = rows['results'] if isinstance(rows, dict) else rows
        self.assertEqual(sorted(x['title'] for x in rows), ['hotel', 'shop'])


class VocabularyTest(TestCase):
    def test_helpers(self):
        self.assertEqual(categories_for_property_type('commercial'), ['commercial', 'hospitality'])
        self.assertEqual(categories_for_property_type('plot'), ['land'])
        self.assertEqual(categories_for_property_type(''), [])
        self.assertEqual(classify_type_text('Flat'), ('residential', 'apartment'))
        self.assertEqual(classify_type_text('plot'), ('land', None))
        self.assertEqual(classify_type_text('castle'), ('residential', None))

    def test_frozen_text_map_matches_taxonomy(self):
        from apps.properties import taxonomy
        for k, v in lc._TEXT_TYPES.items():
            self.assertEqual(taxonomy._TEXT_TYPES[k], v, k)
