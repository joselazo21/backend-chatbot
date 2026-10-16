from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    dependencies = [("customers", "0003_widgetsite")]
    operations = [VectorExtension()]
