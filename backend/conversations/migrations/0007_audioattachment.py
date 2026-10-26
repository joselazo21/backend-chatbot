import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0006_channelconversation_inboundchannelmessage_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='AudioAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('data', models.BinaryField(blank=True, null=True)),
                ('content_type', models.CharField(blank=True, max_length=64)),
                ('telegram_file_id', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('message', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='audio', to='conversations.message')),
            ],
        ),
    ]
