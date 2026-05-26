from unittest.mock import patch
from django.test import TestCase
from apps.users.models import User
from apps.organizations.models import Organization
from apps.whatsapp.models import WhatsAppSession, OrgWhatsAppConfig
from apps.leads.models import Lead
from apps.properties.models import Property


def _make_org():
    return Organization.objects.create(
        name='Test Org', slug='test-org', is_active=True,
    )


def _make_user(phone='+923001234567'):
    return User.objects.get_or_create(phone=phone, defaults={'is_active': True})[0]


def _make_session(user, org):
    return WhatsAppSession.objects.create(
        phone=user.phone, user=user, organization=org,
    )


def _make_lead(user, org):
    return Lead.objects.create(user=user, organization=org)


class DirectoryEntryHandlerTest(TestCase):

    def test_directory_entry_signals_parsed(self):
        """Referral payload stamps discovery metadata onto session + lead."""
        from apps.whatsapp.discovery_engine import DirectoryEntryHandler

        org     = _make_org()
        user    = _make_user()
        session = _make_session(user, org)
        lead    = _make_lead(user, org)

        message_data = {
            'type': 'text',
            'text': {'body': 'Hello'},
            'referral': {
                'source_type': 'CLICK_TO_WHATSAPP',
                'ctwa_clid':   'ARAkLkA8abc123',
                'source_url':  'https://www.facebook.com/ads/12345',
                'source_id':   '12345',
            },
        }

        DirectoryEntryHandler.handle(message_data, session, lead)

        session.refresh_from_db()
        lead.refresh_from_db()

        self.assertEqual(
            session.context.get('discovery_source'), 'CLICK_TO_WHATSAPP'
        )
        self.assertEqual(
            lead.intent_signals.get('ctwa_clid'), 'ARAkLkA8abc123'
        )
        self.assertGreater(lead.score, 0)

    def test_no_referral_is_silent(self):
        """Messages without referral block do not modify session or lead."""
        from apps.whatsapp.discovery_engine import DirectoryEntryHandler

        org     = _make_org()
        user    = _make_user('+923009999999')
        session = _make_session(user, org)
        lead    = _make_lead(user, org)
        original_score = lead.score

        DirectoryEntryHandler.handle({'type': 'text', 'text': {'body': 'Hi'}}, session, lead)

        lead.refresh_from_db()
        self.assertEqual(lead.score, original_score)
        self.assertNotIn('discovery_source', session.context)


class GeoContextResolverTier1Test(TestCase):

    def _make_property(self, org, city='Karachi'):
        owner = User.objects.get_or_create(
            phone='+923001111111', defaults={'is_active': True, 'role': 'client'}
        )[0]
        return Property.objects.create(
            organization=org,
            owner=owner,
            listing_owner_type='organization',
            city=city,
            location='Test Location',
            property_type='apartment',
            area_marla=5,
            area_unit='marla',
            price=5000000,
            currency='PKR',
            is_active=True,
        )

    @patch('apps.whatsapp.discovery_engine.GeoContextResolver._nominatim_lookup')
    def test_location_pin_maps_to_neighbourhood_tier(self, mock_nominatim):
        """Tier 1: Nominatim city -> Property.city ILIKE filter; lead + session updated."""
        from apps.whatsapp.discovery_engine import GeoContextResolver

        mock_nominatim.return_value = 'Karachi'

        org     = _make_org()
        user    = _make_user('+923002222222')
        session = _make_session(user, org)
        lead    = _make_lead(user, org)
        prop    = self._make_property(org, city='Karachi')

        lat, lon = 24.8607, 67.0011
        qs = GeoContextResolver.resolve(lat, lon, org, lead, session)

        lead.refresh_from_db()
        session.refresh_from_db()

        self.assertEqual(lead.city_interest, 'Karachi')
        self.assertAlmostEqual(float(lead.last_known_lat), lat, places=3)
        self.assertAlmostEqual(float(lead.last_known_lon), lon, places=3)

        geo = session.context.get('geo', {})
        self.assertEqual(geo.get('city'), 'Karachi')
        self.assertEqual(geo.get('tier'), 1)

        self.assertIn(prop, list(qs))

    @patch('apps.whatsapp.discovery_engine.GeoContextResolver._nominatim_lookup')
    def test_nominatim_empty_returns_empty_tier1(self, mock_nominatim):
        """When Nominatim returns None, Tier 1 yields no results and falls through."""
        from apps.whatsapp.discovery_engine import GeoContextResolver

        mock_nominatim.return_value = None

        org     = _make_org()
        user    = _make_user('+923003333333')
        session = _make_session(user, org)
        lead    = _make_lead(user, org)

        qs = GeoContextResolver.resolve(0.0, 0.0, org, lead, session)
        session.refresh_from_db()
        geo = session.context.get('geo', {})
        self.assertIsNone(geo.get('tier'))


class GeoContextResolverTier2Test(TestCase):

    def _make_property_with_coords(self, org, lat, lon, city='Karachi'):
        owner = User.objects.get_or_create(
            phone='+923004444444', defaults={'is_active': True, 'role': 'client'}
        )[0]
        return Property.objects.create(
            organization=org,
            owner=owner,
            listing_owner_type='organization',
            city=city,
            location='Near pin',
            property_type='house',
            area_marla=10,
            area_unit='marla',
            price=10000000,
            currency='PKR',
            is_active=True,
            latitude=lat,
            longitude=lon,
        )

    @patch('apps.whatsapp.discovery_engine.GeoContextResolver._nominatim_lookup')
    def test_geo_resolver_tier_fallback_to_haversine(self, mock_nominatim):
        """Tier 2: when Nominatim times out, haversine returns nearby properties."""
        from apps.whatsapp.discovery_engine import GeoContextResolver

        mock_nominatim.side_effect = Exception('timeout')

        org     = _make_org()
        user    = _make_user('+923005555555')
        session = _make_session(user, org)
        lead    = _make_lead(user, org)

        prop = self._make_property_with_coords(org, lat=24.8607, lon=67.0011)

        qs = GeoContextResolver.resolve(24.8650, 67.0050, org, lead, session)

        session.refresh_from_db()
        geo = session.context.get('geo', {})
        self.assertEqual(geo.get('tier'), 2)
        self.assertIn(prop, list(qs))

    @patch('apps.whatsapp.discovery_engine.GeoContextResolver._nominatim_lookup')
    def test_property_beyond_radius_excluded(self, mock_nominatim):
        """Properties > 25 km away are NOT returned in Tier 2."""
        from apps.whatsapp.discovery_engine import GeoContextResolver

        mock_nominatim.return_value = None

        org     = _make_org()
        user    = _make_user('+923006666666')
        session = _make_session(user, org)
        lead    = _make_lead(user, org)

        prop = self._make_property_with_coords(org, lat=31.5204, lon=74.3587, city='Lahore')

        qs = GeoContextResolver.resolve(24.8607, 67.0011, org, lead, session)

        self.assertNotIn(prop, list(qs))
