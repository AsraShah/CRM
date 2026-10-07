"""Backfill Membership.mfa_enrolled from allauth's authenticators.

Until the identity signal receivers existed, enrolling a second factor never
set this flag, so anyone who enrolled earlier is still recorded as not enrolled
and locked out of the capabilities it gates. This sets the flag once from the
authenticators that actually exist. Recovery codes alone do not count.
"""

from django.db import migrations

REAL_FACTORS = ["totp", "webauthn"]


def forwards(apps, schema_editor):
    Authenticator = apps.get_model("mfa", "Authenticator")
    Membership = apps.get_model("identity", "Membership")

    enrolled_users = Authenticator.objects.filter(type__in=REAL_FACTORS).values("user_id")
    # Only ever grants. A flag someone set by hand is left alone rather than
    # silently revoked on migrate; from now on the receivers keep it accurate.
    Membership.objects.filter(user_id__in=enrolled_users).update(mfa_enrolled=True)


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0001_initial"),
        ("mfa", "0003_authenticator_type_uniq"),
    ]

    # Reversing leaves the flags as they are: they now describe reality.
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
