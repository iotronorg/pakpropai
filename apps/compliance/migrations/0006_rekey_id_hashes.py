"""KI-102: ID numbers were stored as plain SHA-256, which can be reversed by trying every number."""
from django.db import migrations


def rekey(apps, schema_editor):
    from apps.core.crypto import blind_index
    for name in ('ComplianceSanctionRecord', 'SanctionScreeningResult'):
        Model = apps.get_model('compliance', name)
        batch = []
        for row in Model.objects.exclude(id_number_hash='').only('pk', 'id_number_hash').iterator():
            row.id_number_hash = blind_index(row.id_number_hash)
            batch.append(row)
        Model.objects.bulk_update(batch, ['id_number_hash'], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [('compliance', '0005_private_data_exports')]

    operations = [migrations.RunPython(rekey, migrations.RunPython.noop)]
