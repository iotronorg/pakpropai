"""
Management command: python manage.py seed_data

Creates a minimal, reproducible local dev dataset:
  - 1 admin user
  - 1 agent user + Agent profile
  - 1 developer org user + Agent profile (agency type)
  - 1 client user (for WhatsApp flow testing)
  - 5 properties (owned by agent user)
  - 10 leads (mix of intents/statuses, assigned to agent)

Safe to run multiple times — uses get_or_create throughout.
Login works locally without WhatsApp: OTP is always logged at WARNING level.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone


PHONES = {
    'admin':     '+923000000001',
    'agent':     '+923000000002',
    'developer': '+923000000003',
    'client':    '+923000000004',
    'client2':   '+923000000005',
}


class Command(BaseCommand):
    help = 'Seed local dev database with test users, properties, and leads.'

    def handle(self, *args, **options):
        from apps.users.models import User
        from apps.agents.models import Agent
        from apps.properties.models import Property
        from apps.leads.models import Lead

        self.stdout.write(self.style.MIGRATE_HEADING('\n── Seeding PakProp AI local dev data ──\n'))

        # ── Users ──────────────────────────────────────────────────────────────
        admin = self._make_user(User, PHONES['admin'],     'admin',     'PakProp Admin',   is_staff=True)
        agent_user = self._make_user(User, PHONES['agent'], 'agent',    'Ahmed Khan')
        dev_user   = self._make_user(User, PHONES['developer'], 'developer', 'Bahria Developments')
        client     = self._make_user(User, PHONES['client'], 'user',    'Ali Hassan')
        client2    = self._make_user(User, PHONES['client2'], 'user',   'Sara Malik')

        # ── Agent profile ──────────────────────────────────────────────────────
        agent_profile, created = Agent.objects.get_or_create(
            user=agent_user,
            defaults={
                'name':            'Ahmed Khan',
                'agent_type':      Agent.AgentType.INDIVIDUAL,
                'phone':           PHONES['agent'],
                'email':           'ahmed@pakprop.local',
                'bio':             'Senior property consultant with 8 years in Lahore real estate.',
                'years_experience': 8,
                'languages':       ['Urdu', 'English', 'Punjabi'],
                'specializations': ['residential_buy', 'plots', 'residential_rent'],
                'cities':          ['Lahore', 'Islamabad'],
                'areas':           ['DHA Phase 5', 'DHA Phase 6', 'Bahria Town', 'Gulberg III', 'F-7'],
                'primary_city':    'Lahore',
                'is_verified':     True,
                'is_active':       True,
                'is_featured':     True,
                'verified_at':     timezone.now(),
                'verified_by':     admin,
                'total_leads':     10,
                'total_listings':  5,
                'closed_deals':    3,
                'rating':          4.5,
            }
        )
        self._log('Agent profile', 'Ahmed Khan', created)

        # ── Developer org profile ──────────────────────────────────────────────
        dev_org, created = Agent.objects.get_or_create(
            user=dev_user,
            defaults={
                'name':                'Bahria Developments',
                'agent_type':          Agent.AgentType.AGENCY,
                'phone':               PHONES['developer'],
                'email':               'info@bahriadev.local',
                'company_name':        'Bahria Developments Pvt Ltd',
                'designation':         'Head Office',
                'bio':                 'Leading real estate developer operating in major cities of Pakistan.',
                'years_experience':    15,
                'languages':           ['Urdu', 'English'],
                'specializations':     ['new_projects', 'plots', 'residential_buy'],
                'cities':              ['Lahore', 'Islamabad', 'Rawalpindi', 'Karachi'],
                'areas':               ['Bahria Town Lahore', 'Bahria Phase 8', 'Bahria Enclave'],
                'primary_city':        'Lahore',
                'registration_number': 'SECP-2009-12345',
                'ntn_number':          '1234567-8',
                'website':             'https://bahriadevelopments.local',
                'is_verified':         True,
                'is_active':           True,
                'is_featured':         True,
                'verified_at':         timezone.now(),
                'verified_by':         admin,
            }
        )
        self._log('Developer org profile', 'Bahria Developments', created)

        # ── Properties ─────────────────────────────────────────────────────────
        properties_data = [
            {
                'title':               '5 Marla House — DHA Phase 5, Lahore',
                'description':         '3-bedroom, 2-bathroom house in prime DHA Phase 5 location. Near commercial area.',
                'city':                'Lahore',
                'location':            'DHA Phase 5, Block L',
                'area_marla':          5,
                'price':           25_000_000,
                'property_type':       Property.PropertyType.RESIDENTIAL,
                'furnished_status':    Property.FurnishedStatus.UNFURNISHED,
                'construction_status': Property.ConstructionStatus.READY,
                'legal_status':        Property.LegalStatus.VERIFIED,
                'ai_score':            82,
                'risk_level':          Property.RiskLevel.LOW,
                'owner':               agent_user,
                'assigned_agent':      agent_profile,
            },
            {
                'title':               '10 Marla Plot — Bahria Town Lahore',
                'description':         'Residential plot in Bahria Town, sector C. All utilities available.',
                'city':                'Lahore',
                'location':            'Bahria Town, Sector C',
                'area_marla':          10,
                'price':           18_000_000,
                'property_type':       Property.PropertyType.PLOT,
                'furnished_status':    None,
                'construction_status': None,
                'legal_status':        Property.LegalStatus.VERIFIED,
                'ai_score':            75,
                'risk_level':          Property.RiskLevel.LOW,
                'owner':               agent_user,
                'assigned_agent':      agent_profile,
            },
            {
                'title':               '2 Marla Commercial Shop — Gulberg III, Lahore',
                'description':         'Ground floor commercial shop on MM Alam Road with high foot traffic.',
                'city':                'Lahore',
                'location':            'Gulberg III, MM Alam Road',
                'area_marla':          2,
                'price':           35_000_000,
                'property_type':       Property.PropertyType.COMMERCIAL,
                'furnished_status':    None,
                'construction_status': Property.ConstructionStatus.READY,
                'legal_status':        Property.LegalStatus.UNVERIFIED,
                'ai_score':            65,
                'risk_level':          Property.RiskLevel.MEDIUM,
                'owner':               agent_user,
                'assigned_agent':      agent_profile,
            },
            {
                'title':               '1 Kanal House — F-7/2, Islamabad',
                'description':         'Fully furnished luxury house in F-7. 5 bedrooms, servant quarters, garden.',
                'city':                'Islamabad',
                'location':            'F-7/2',
                'area_marla':          20,
                'price':           80_000_000,
                'property_type':       Property.PropertyType.RESIDENTIAL,
                'furnished_status':    Property.FurnishedStatus.FURNISHED,
                'construction_status': Property.ConstructionStatus.READY,
                'legal_status':        Property.LegalStatus.VERIFIED,
                'ai_score':            91,
                'risk_level':          Property.RiskLevel.LOW,
                'owner':               agent_user,
                'assigned_agent':      agent_profile,
            },
            {
                'title':               '5 Marla House — Bahria Phase 8, Rawalpindi (UC)',
                'description':         'Under construction 3-bed house. Possession in 12 months. Installments available.',
                'city':                'Rawalpindi',
                'location':            'Bahria Phase 8, Block C',
                'area_marla':          5,
                'price':           12_000_000,
                'property_type':       Property.PropertyType.RESIDENTIAL,
                'furnished_status':    Property.FurnishedStatus.UNFURNISHED,
                'construction_status': Property.ConstructionStatus.UNDER_CONSTRUCTION,
                'legal_status':        Property.LegalStatus.PENDING,
                'ai_score':            58,
                'risk_level':          Property.RiskLevel.MEDIUM,
                'owner':               dev_user,
                'assigned_agent':      dev_org,
            },
        ]

        created_properties = []
        for data in properties_data:
            prop, created = Property.objects.get_or_create(
                title=data['title'],
                defaults=data,
            )
            self._log('Property', prop.title[:50], created)
            created_properties.append(prop)

        # ── Leads ──────────────────────────────────────────────────────────────
        leads_data = [
            # client leads
            {'user': client,  'intent': Lead.Intent.BUY,    'status': Lead.Status.WARM,      'score': 78, 'city_interest': 'Lahore',     'budget_min': 15_000_000, 'budget_max': 30_000_000, 'notes': 'Interested in DHA Phase 5. Wants 5 marla house.'},
            {'user': client,  'intent': Lead.Intent.INVEST,  'status': Lead.Status.QUALIFIED, 'score': 85, 'city_interest': 'Lahore',     'budget_min': 10_000_000, 'budget_max': 20_000_000, 'notes': 'Looking for plot investment in Bahria Town.'},
            {'user': client2, 'intent': Lead.Intent.RENT,    'status': Lead.Status.NEW,       'score': 42, 'city_interest': 'Islamabad',  'budget_min': 80_000,     'budget_max': 150_000,    'notes': 'Needs 3-bed furnished house in F-7 or F-8.'},
            {'user': client2, 'intent': Lead.Intent.BUY,     'status': Lead.Status.COLD,      'score': 21, 'city_interest': 'Rawalpindi', 'budget_min': 8_000_000,  'budget_max': 15_000_000, 'notes': 'Inquired about Bahria Phase 8. Went cold.'},
            # extra leads without assigned agent (unassigned pool)
            {'user': admin,   'intent': Lead.Intent.SELL,    'status': Lead.Status.NEW,       'score': 55, 'city_interest': 'Lahore',     'budget_min': None,        'budget_max': None,        'notes': 'Wants to sell 10 marla property in Johar Town.'},
            {'user': admin,   'intent': Lead.Intent.LOAN,    'status': Lead.Status.WARM,      'score': 60, 'city_interest': 'Karachi',    'budget_min': 5_000_000,  'budget_max': 10_000_000, 'notes': 'Asking about Apna Ghar scheme eligibility.'},
            {'user': admin,   'intent': Lead.Intent.TAX,     'status': Lead.Status.QUALIFIED, 'score': 70, 'city_interest': 'Islamabad',  'budget_min': None,        'budget_max': None,        'notes': 'Overseas Pakistani, asking about 7E tax.'},
            {'user': admin,   'intent': Lead.Intent.BUY,     'status': Lead.Status.NEW,       'score': 35, 'city_interest': 'Lahore',     'budget_min': 20_000_000, 'budget_max': 50_000_000, 'notes': 'High budget buyer, looking for 1 kanal in DHA.'},
            {'user': client,  'intent': Lead.Intent.INVEST,  'status': Lead.Status.NEW,       'score': 48, 'city_interest': 'Karachi',    'budget_min': 5_000_000,  'budget_max': 12_000_000, 'notes': 'Interested in commercial investment in Karachi.'},
            {'user': client2, 'intent': Lead.Intent.BUY,     'status': Lead.Status.WARM,      'score': 65, 'city_interest': 'Lahore',     'budget_min': 10_000_000, 'budget_max': 18_000_000, 'notes': 'Wants a plot, Bahria or DHA. Flexible on location.'},
        ]

        for i, data in enumerate(leads_data):
            assigned = agent_profile if i < 4 else None
            Lead.objects.get_or_create(
                user=data['user'],
                intent=data['intent'],
                city_interest=data['city_interest'],
                defaults={
                    'assigned_agent': assigned,
                    'status':         data['status'],
                    'score':          data['score'],
                    'budget_min':     data['budget_min'],
                    'budget_max':     data['budget_max'],
                    'notes':          data['notes'],
                },
            )

        lead_count = Lead.objects.count()
        self.stdout.write(f"  Leads: {lead_count} total in database")

        # ── Summary ────────────────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS('\n── Seed complete ──'))
        self.stdout.write('\nLogin credentials (use OTP — code logged at WARNING level):')
        self.stdout.write(f"  Admin:     {PHONES['admin']}")
        self.stdout.write(f"  Agent:     {PHONES['agent']}")
        self.stdout.write(f"  Developer: {PHONES['developer']}")
        self.stdout.write(f"  Client:    {PHONES['client']}")
        self.stdout.write('\nAll OTP codes are printed in the Django server log (search for "OTP for").\n')

    def _make_user(self, User, phone, role, name, is_staff=False):
        user, created = User.objects.get_or_create(
            phone=phone,
            defaults={
                'name':     name,
                'role':     role,
                'is_staff': is_staff,
                'is_active': True,
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=['password'])
        self._log('User', f"{name} ({role}) {phone}", created)
        return user

    def _log(self, kind, label, created):
        if created:
            self.stdout.write(f"  {self.style.SUCCESS('created')} {kind}: {label}")
        else:
            self.stdout.write(f"  {self.style.WARNING('exists')}  {kind}: {label}")
