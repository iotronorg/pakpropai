#!/usr/bin/env python3
"""
Data for the end-to-end journey: one organization, its agent with free hours, two
listings. Idempotent. ``--remove`` deletes it, with the enquiries and viewings made on it.

Refuses to run unless E2E_SEED_DATABASE names the connected database. The agent is
a real WhatsApp recipient outside a local run: set E2E_AGENT_PHONE to a phone you own.
"""
import os
import sys
from datetime import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ORG = 'E2E Journey Estates'


def main():
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.db import connection, transaction

    from apps.agents.models import Agent
    from apps.enquiries.models import Enquiry
    from apps.leads.models import Appointment, Lead
    from apps.organizations.models import Organization, OrganizationMembership
    from apps.properties.models import Property
    from apps.viewings.models import AvailabilityRule

    name = connection.settings_dict['NAME']
    if os.environ.get('E2E_SEED_DATABASE') != name:
        sys.exit(f'Set E2E_SEED_DATABASE={name} to seed this database.')
    agent_phone = os.environ.get('E2E_AGENT_PHONE') or sys.exit('Set E2E_AGENT_PHONE.')
    with transaction.atomic():
        if '--remove' in sys.argv:
            org = Organization.objects.filter(name=ORG).first()
            if org:
                Appointment.objects.filter(property__organization=org).delete()
                Enquiry.objects.filter(organization=org).delete()
                Lead.objects.filter(organization=org).delete()
                Property.objects.filter(organization=org).delete()
                Agent.objects.filter(organization=org).delete()
                org.delete()
            print('removed')
            return
        user, _ = get_user_model().objects.get_or_create(phone=agent_phone, defaults={'role': 'agent', 'name': 'E2E Agent'})
        if user.role != 'agent':
            sys.exit('E2E_AGENT_PHONE belongs to another kind of account.')
        org, _ = Organization.objects.get_or_create(name=ORG, defaults={'country': 'PK', 'operational_mode': 'production'})
        OrganizationMembership.objects.get_or_create(user=user, organization=org, defaults={'role': 'agent'})
        agent, _ = Agent.objects.get_or_create(user=user, defaults={
            'organization': org, 'name': 'E2E Agent', 'phone': agent_phone, 'employment_type': 'internal'})
        if agent.organization_id != org.pk:
            sys.exit('E2E_AGENT_PHONE is an agent of another organization.')
        Agent.objects.filter(pk=agent.pk).update(is_active=True)
        for day in range(7):
            AvailabilityRule.objects.get_or_create(agent=agent, weekday=day, start=time(9), end=time(20),
                                                   defaults={'timezone': 'Asia/Karachi'})
        for n, price in ((1, 20_000_000), (2, 30_000_000)):
            p, _ = Property.objects.get_or_create(organization=org, title=f'E2E House {n}', defaults={
                'city': 'Lahore', 'location': 'DHA Phase 5', 'price': price, 'category': 'residential', 'size_value': 10,
                'size_unit': 'marla', 'bedrooms': 4, 'purpose': 'sale', 'listing_owner_type': 'organization',
                'listed_by_agent': agent, 'description': 'e2ejourney test listing', 'country': 'PK'})
            if p.is_sandbox:
                sys.exit('The listing is in the sandbox.')
    print('seeded')


if __name__ == '__main__':
    main()
