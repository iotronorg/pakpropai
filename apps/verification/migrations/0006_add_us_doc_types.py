from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('verification', '0005_add_global_doc_types'),
    ]

    operations = [
        migrations.AlterField(
            model_name='documentscan',
            name='document_type',
            field=__import__('django.db.models', fromlist=['CharField']).CharField(
                choices=[
                    ('fard', 'Fard (Ownership Record)'),
                    ('allotment', 'Allotment Letter'),
                    ('sale_deed', 'Sale Deed / Registry'),
                    ('noc', 'No Objection Certificate'),
                    ('tax_cert', 'Tax Certificate'),
                    ('cnic', 'CNIC'),
                    ('poa', 'Power of Attorney'),
                    ('title_deed', 'Title Deed (DLD)'),
                    ('oqood', 'Oqood (Off-plan Registration)'),
                    ('emirates_id', 'Emirates ID'),
                    ('title_register', 'Title Register (HM Land Registry)'),
                    ('land_certificate', 'Land Certificate'),
                    ('mortgage_deed', 'Mortgage Deed'),
                    ('warranty_deed', 'Warranty Deed'),
                    ('title_insurance', 'Title Insurance Policy'),
                    ('hoa_docs', 'HOA Documents'),
                    ('closing_disclosure', 'Closing Disclosure (HUD-1)'),
                    ('promissory_note', 'Promissory Note / Mortgage Note'),
                    ('drivers_license', "Driver's License"),
                    ('state_id', 'State ID'),
                    ('passport', 'Passport'),
                    ('driving_licence', 'Driving Licence'),
                    ('other', 'Other Document'),
                ],
                default='other',
                max_length=20,
            ),
        ),
    ]
