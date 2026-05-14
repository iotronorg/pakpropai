from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0002_role_model_relationships'),
    ]

    operations = [
        migrations.AddField(
            model_name='agent',
            name='registration_status',
            field=models.CharField(
                choices=[
                    ('pending',  'Pending Approval'),
                    ('approved', 'Approved'),
                    ('rejected', 'Rejected'),
                ],
                default='approved',   # existing agents are already live
                max_length=20,
                help_text='Approval state — pending until admin/developer reviews the application',
            ),
        ),
        migrations.AddField(
            model_name='agent',
            name='rejection_reason',
            field=models.TextField(
                blank=True,
                help_text='Reason shown to the agent when their application is rejected',
            ),
        ),
        # New registrations default to pending; existing rows stay approved.
        # The model default is 'pending' — migration uses 'approved' so live
        # agents are not accidentally locked out.
    ]
