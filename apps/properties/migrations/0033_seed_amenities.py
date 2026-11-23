"""
Seed the amenity taxonomy (T-1070d). Idempotent (get_or_create by key, so
admin edits survive a re-run). Labels: en + ur (PK's languages; the other
markets are en-only). `markets` empty = every market. Reverse removes only
seeded keys that no listing uses.
"""
from django.db import migrations

# (key, group, en, ur, markets)
AMENITIES = [
    ('air_conditioning',   'interior',      'Air conditioning',     'ایئر کنڈیشنگ',        []),
    ('central_heating',    'interior',      'Central heating',      'سینٹرل ہیٹنگ',        []),
    ('fitted_kitchen',     'interior',      'Fitted kitchen',       'فٹڈ کچن',             []),
    ('built_in_wardrobes', 'interior',      'Built-in wardrobes',   'بلٹ ان الماریاں',     []),
    ('drawing_room',       'interior',      'Drawing room',         'ڈرائنگ روم',          ['PK']),
    ('dining_room',        'interior',      'Dining room',          'ڈائننگ روم',          []),
    ('study_room',         'interior',      'Study room',           'اسٹڈی روم',           []),
    ('store_room',         'interior',      'Store room',           'اسٹور روم',           []),
    ('laundry_room',       'interior',      'Laundry room',         'لانڈری روم',          []),
    ('servant_quarters',   'interior',      'Servant quarters',     'سرونٹ کوارٹر',        ['PK']),
    ('maids_room',         'interior',      "Maid's room",          'میڈ روم',             ['AE']),
    ('basement',           'interior',      'Basement',             'تہہ خانہ',            []),
    ('balcony',            'outdoor',       'Balcony',              'بالکونی',             []),
    ('terrace',            'outdoor',       'Terrace',              'ٹیرس',                []),
    ('garden',             'outdoor',       'Garden / lawn',        'لان',                 []),
    ('private_pool',       'outdoor',       'Private pool',         'نجی سوئمنگ پول',      []),
    ('lift',               'building',      'Lift',                 'لفٹ',                 []),
    ('covered_parking',    'building',      'Covered parking',      'کورڈ پارکنگ',         []),
    ('gym',                'building',      'Gym',                  'جم',                  []),
    ('swimming_pool',      'building',      'Shared swimming pool', 'سوئمنگ پول',          []),
    ('concierge',          'building',      'Concierge',            'کنسیئرج',             []),
    ('intercom',           'building',      'Intercom',             'انٹرکام',             []),
    ('security_guard',     'security',      'Security guard',       'سیکیورٹی گارڈ',       []),
    ('cctv',               'security',      'CCTV',                 'سی سی ٹی وی',         []),
    ('gated_community',    'security',      'Gated community',      'گیٹڈ کمیونٹی',        []),
    ('fire_alarm',         'security',      'Fire alarm',           'فائر الارم',          []),
    ('backup_generator',   'utilities',     'Backup generator',     'بیک اپ جنریٹر',       []),
    ('solar_panels',       'utilities',     'Solar panels',         'سولر پینل',           []),
    ('gas_connection',     'utilities',     'Gas connection',       'گیس کنکشن',           ['PK']),
    ('water_tank',         'utilities',     'Water tank',           'پانی کی ٹینکی',       []),
    ('broadband',          'utilities',     'Broadband internet',   'براڈ بینڈ انٹرنیٹ',   []),
    ('park',               'community',     'Park',                 'پارک',                []),
    ('kids_play_area',     'community',     "Children's play area", 'بچوں کے کھیلنے کی جگہ', []),
    ('community_mosque',   'community',     'Mosque in community',  'کمیونٹی مسجد',        []),
    ('wheelchair_access',  'accessibility', 'Wheelchair access',    'وہیل چیئر رسائی',     []),
]


def seed(apps, schema_editor):
    Amenity = apps.get_model('properties', 'Amenity')
    for order, (key, group, en, ur, markets) in enumerate(AMENITIES):
        Amenity.objects.get_or_create(key=key, defaults=dict(
            group=group, labels={'en': en, 'ur': ur}, markets=markets, sort_order=order))


def unseed(apps, schema_editor):
    Amenity = apps.get_model('properties', 'Amenity')
    ListingAmenity = apps.get_model('properties', 'ListingAmenity')
    used = ListingAmenity.objects.values('amenity_id')
    Amenity.objects.filter(key__in=[a[0] for a in AMENITIES]).exclude(id__in=used).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('properties', '0032_backfill_category'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
