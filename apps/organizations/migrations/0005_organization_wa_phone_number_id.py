from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0004_rename_org_type_idx_organizatio_org_typ_b2fc82_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='wa_phone_number_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="WhatsApp Cloud API phone_number_id for this org's dedicated WA number",
                max_length=50,
            ),
        ),
    ]
