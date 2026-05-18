from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AuditBenchmark, PropertyAudit


class IsAdmin(IsAuthenticated):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.role == 'admin'


class AuditListView(APIView):
    """GET /audit/ — admin list of all property audits."""
    permission_classes = [IsAdmin]

    def get(self, request):
        qs = PropertyAudit.objects.select_related('user').order_by('-created_at')[:100]
        return Response([{
            'id':               a.id,
            'phone':            a.phone,
            'city':             a.city,
            'location':         a.location,
            'property_type':    a.property_type,
            'area_size':          float(a.area_size) if a.area_size else None,
            'area_unit':          a.area_unit,
            'estimated_value':    a.estimated_value,
            'currency':           a.currency,
            'risk_score':       a.risk_score,
            'investment_grade': a.investment_grade,
            'liquidity_score':  a.liquidity_score,
            'has_pdf':          bool(a.pdf_file),
            'created_at':       a.created_at.isoformat(),
        } for a in qs])


def download_pdf(request, audit_id):
    if not request.user.is_authenticated:
        return HttpResponseForbidden("Authentication required.")
    audit = get_object_or_404(PropertyAudit, id=audit_id)
    if not audit.pdf_file:
        raise Http404
    return HttpResponseRedirect(audit.pdf_file.url)


def _serialize_benchmark(b):
    return {
        'id':           b.id,
        'city':         b.city,
        'location_key': b.location_key,
        'price_per_unit_min': b.price_per_unit_min,
        'price_per_unit_max': b.price_per_unit_max,
        'size_unit':          b.size_unit,
        'currency':           b.currency,
        'country':            b.country,
        'yield_pct':    b.yield_pct,
        'appr_pct':     b.appr_pct,
        'liq_months':   b.liq_months,
        'approved':     b.approved,
        'is_active':    b.is_active,
        'updated_at':   b.updated_at.isoformat(),
    }


class BenchmarkListView(APIView):
    """
    GET  /audit/benchmarks/  — list all benchmarks (admin only)
    POST /audit/benchmarks/  — create a new benchmark (admin only)
    """
    permission_classes = [IsAdmin]

    def get(self, request):
        qs = AuditBenchmark.objects.all()
        city = request.query_params.get('city')
        if city:
            qs = qs.filter(city=city.lower().strip())
        return Response([_serialize_benchmark(b) for b in qs])

    def post(self, request):
        data = request.data
        required = ('city', 'location_key', 'price_per_unit_min', 'price_per_unit_max', 'yield_pct', 'appr_pct', 'liq_months')
        missing = [f for f in required if f not in data]
        if missing:
            return Response({'error': f"Missing fields: {missing}"}, status=status.HTTP_400_BAD_REQUEST)

        country = data.get('country', 'PK').upper().strip()
        if AuditBenchmark.objects.filter(
            country=country,
            city=data['city'].lower().strip(),
            location_key=data['location_key'].lower().strip(),
        ).exists():
            return Response(
                {'error': 'A benchmark for this country + city + location_key already exists. Use PATCH to update it.'},
                status=status.HTTP_409_CONFLICT,
            )

        b = AuditBenchmark.objects.create(
            country=country,
            city=data['city'].lower().strip(),
            location_key=data['location_key'].lower().strip(),
            price_per_unit_min=data['price_per_unit_min'],
            price_per_unit_max=data['price_per_unit_max'],
            size_unit=data.get('size_unit', 'marla'),
            currency=data.get('currency', 'PKR').upper(),
            yield_pct=data['yield_pct'],
            appr_pct=data['appr_pct'],
            liq_months=data['liq_months'],
            approved=data.get('approved'),
            is_active=data.get('is_active', True),
            updated_by=request.user,
        )
        return Response(_serialize_benchmark(b), status=status.HTTP_201_CREATED)


class BenchmarkDetailView(APIView):
    """
    PATCH  /audit/benchmarks/<id>/  — update a benchmark (admin only)
    DELETE /audit/benchmarks/<id>/  — delete a benchmark (admin only)
    """
    permission_classes = [IsAdmin]

    _UPDATABLE = ('price_per_unit_min', 'price_per_unit_max', 'size_unit', 'currency',
                  'yield_pct', 'appr_pct', 'liq_months', 'approved', 'is_active')

    def patch(self, request, benchmark_id):
        b = get_object_or_404(AuditBenchmark, id=benchmark_id)
        for field in self._UPDATABLE:
            if field in request.data:
                setattr(b, field, request.data[field])
        b.updated_by = request.user
        b.save()
        return Response(_serialize_benchmark(b))

    def delete(self, request, benchmark_id):
        b = get_object_or_404(AuditBenchmark, id=benchmark_id)
        b.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
