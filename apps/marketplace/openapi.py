"""OpenAPI descriptions of the hand-built marketplace endpoints (T-1091, KI-037); keep in step with ``views.py``."""
from drf_spectacular.utils import OpenApiResponse, extend_schema

ERROR = OpenApiResponse(description='Refused; `detail` and `error_info` (ADR-017)')


def browse(view_method):
    from .serializers import SyndicationListingSerializer
    return extend_schema(operation_id='marketplace_browse_list',
                         responses={200: SyndicationListingSerializer(many=True), 403: ERROR},
                         description='Syndicated listings visible to the caller\'s agency or agent; '
                                     'not for organization owners.')(view_method)
