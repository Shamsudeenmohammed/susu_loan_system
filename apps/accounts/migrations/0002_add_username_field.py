# Generated migration to add username field and change USERNAME_FIELD

from django.db import migrations, models


def populate_username_from_email(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    for user in User.objects.all():
        if user.email:
            # Use email prefix as username, ensure uniqueness
            base_username = user.email.split('@')[0].lower()
            username = base_username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1
            user.username = username
        else:
            # Fallback: use first_name + last_name + id
            name = (user.first_name + user.last_name).lower().replace(' ', '')
            if not name:
                name = f'user{user.id}'
            username = name
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{name}{counter}"
                counter += 1
            user.username = username
        user.save(update_fields=['username'])


def reverse_populate(apps, schema_editor):
    # No reverse needed for data migration
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        # Step 1: Add username field as nullable
        migrations.AddField(
            model_name='user',
            name='username',
            field=models.CharField(max_length=150, blank=True, null=True),
        ),
        # Step 2: Populate username from email
        migrations.RunPython(populate_username_from_email, reverse_populate),
        # Step 3: Make username unique and non-nullable
        migrations.AlterField(
            model_name='user',
            name='username',
            field=models.CharField(max_length=150, unique=True),
        ),
        # Step 4: Remove unique constraint from email and make it nullable/blank
        migrations.AlterField(
            model_name='user',
            name='email',
            field=models.EmailField(max_length=254, blank=True, null=True),
        ),
    ]