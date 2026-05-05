from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import FraudCheckService


class FraudCheckView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        query = (request.data.get('query') or '').strip()
        if not query:
            return Response({'error': 'query is required'}, status=400)
        if len(query) > 500:
            return Response({'error': 'query too long (max 500 chars)'}, status=400)

        result = FraudCheckService.check(query, user=request.user)
        return Response(result)