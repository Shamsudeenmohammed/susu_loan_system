from django.db import migrations

SCHOOLS = [
    {
        'code': 'MANKARIGU',
        'name': 'Zemzem Golden Child Academy \u2013 Mankarigu',
        'short_name': 'Zemzem Mankarigu',
        'location': 'Mankarigu',
    },
    {
        'code': 'LINGBINSI',
        'name': 'Zemzem Golden Child Academy \u2013 Lingbinsi',
        'short_name': 'Zemzem Lingbinsi',
        'location': 'Lingbinsi',
    },
]


def create_schools(apps, schema_editor):
    School = apps.get_model('schools', 'School')
    for spec in SCHOOLS:
        School.objects.update_or_create(
            code=spec['code'],
            defaults={
                'name': spec['name'],
                'short_name': spec['short_name'],
                'location': spec['location'],
                'is_active': True,
            },
        )


def remove_schools(apps, schema_editor):
    School = apps.get_model('schools', 'School')
    School.objects.filter(code__in=[s['code'] for s in SCHOOLS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('schools', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_schools, remove_schools),
    ]
