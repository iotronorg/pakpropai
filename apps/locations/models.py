"""
Location hierarchy (T-1060a, FR-LST-003, ADR-024).

country → region → city → locality → sub_locality, one tree per market.
``path`` is a materialised slug path (``pk/punjab/lahore/dha-phase-5``) so
"this place and everything under it" is one indexed prefix query.
Rows created by listing matching start unverified (``is_verified=False``):
that is the review queue, worked in admin (verify, or merge into the right
node, which keeps the old name as an alias and re-points listings).
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify


class Location(models.Model):

    class Level(models.TextChoices):
        COUNTRY      = 'country',      'Country'
        REGION       = 'region',       'Region / province / emirate'
        CITY         = 'city',         'City'
        LOCALITY     = 'locality',     'Locality / society / area'
        SUB_LOCALITY = 'sub_locality', 'Sub-locality / phase / block'

    LEVEL_ORDER = ['country', 'region', 'city', 'locality', 'sub_locality']

    class Source(models.TextChoices):
        SEED     = 'seed',     'Seed data'
        ADMIN    = 'admin',    'Admin'
        LISTING  = 'listing',  'Listing match (needs review)'
        GEOCODER = 'geocoder', 'Geocoder'

    market      = models.ForeignKey('markets.Market', on_delete=models.PROTECT, related_name='locations')
    parent      = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='children')
    level       = models.CharField(max_length=15, choices=Level.choices)
    name        = models.CharField(max_length=150)
    slug        = models.SlugField(max_length=160, allow_unicode=True)
    path        = models.CharField(max_length=800, unique=True, editable=False,
                                   help_text='Materialised slug path, e.g. pk/punjab/lahore/dha-phase-5')
    match_key   = models.CharField(max_length=200, db_index=True, editable=False,
                                   help_text='Normalised name used by listing matching')
    aliases     = models.JSONField(default=list, blank=True,
                                   help_text='Other spellings; matched like the name')
    latitude    = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude   = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    # Geocoding (T-1060c). Google terms: coordinates may be cached at most 30
    # days (re-geocoded after GEOCODING_REFRESH_DAYS); the place id may be kept.
    class GeocodeStatus(models.TextChoices):
        PENDING  = 'pending',  'Not geocoded yet'
        OK       = 'ok',       'Geocoded'
        PARTIAL  = 'partial',  'Partial match (needs review; centroid not stored)'
        REJECTED = 'rejected', 'Result failed sanity checks'
        ZERO     = 'zero',     'No result'
        ERROR    = 'error',    'Provider error'
        MANUAL   = 'manual',   'Set by an admin (never re-geocoded)'

    geocode_status   = models.CharField(max_length=10, choices=GeocodeStatus.choices,
                                        default=GeocodeStatus.PENDING, db_default='pending')
    geocoded_at      = models.DateTimeField(null=True, blank=True)
    geocode_place_id = models.CharField(max_length=300, blank=True, default='', db_default='')
    geocode_note     = models.CharField(max_length=200, blank=True, default='', db_default='')
    is_verified = models.BooleanField(default=True)
    is_active   = models.BooleanField(default=True)
    source      = models.CharField(max_length=10, choices=Source.choices, default=Source.ADMIN)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table    = 'locations'
        ordering    = ['path']
        constraints = [models.UniqueConstraint(fields=['parent', 'slug'], name='uniq_location_slug_per_parent')]
        indexes     = [models.Index(fields=['market', 'level', 'match_key'], name='loc_market_level_key_idx'),
                       models.Index(fields=['is_verified', 'level'], name='loc_review_idx'),
                       models.Index(fields=['geocode_status', 'geocoded_at'], name='loc_geocode_idx')]

    def clean(self):
        errors = {}
        if self.level == self.Level.COUNTRY:
            if self.parent_id:
                errors['parent'] = 'A country has no parent.'
        elif self.parent is None:
            errors['parent'] = 'Required below country level.'
        else:
            if self.LEVEL_ORDER.index(self.parent.level) >= self.LEVEL_ORDER.index(self.level):
                errors['parent'] = f'A {self.level} must sit under a broader level.'
            if self.parent.market_id != self.market_id:
                errors['market'] = 'Must match the parent location market.'
        if not isinstance(self.aliases, list) or not all(isinstance(a, str) for a in self.aliases):
            errors['aliases'] = 'A list of names.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        from .services import normalise
        self.slug = self.slug or slugify(self.name, allow_unicode=True)[:160] or 'x'
        prefix = self.parent.path if self.parent_id else self.market_id.lower()
        old_path = None
        if self.pk:
            old_path = Location.objects.filter(pk=self.pk).values_list('path', flat=True).first()
        self.path = f'{prefix}/{self.slug}' if self.parent_id else prefix
        self.match_key = normalise(self.name)
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = list(set(update_fields) | {'slug', 'path', 'match_key'})
        super().save(*args, **kwargs)
        if old_path and old_path != self.path:
            self._rewrite_descendant_paths(old_path)

    def _rewrite_descendant_paths(self, old_path):
        from django.db.models import Value
        from django.db.models.functions import Concat, Substr
        (Location.objects.filter(path__startswith=old_path + '/')
         .update(path=Concat(Value(self.path), Substr('path', len(old_path) + 1))))

    def descendants_q(self, field='path'):
        """``Q`` for this location and everything under it (on a ``path`` field)."""
        return models.Q(**{field: self.path}) | models.Q(**{f'{field}__startswith': self.path + '/'})

    def __str__(self):
        return self.path
