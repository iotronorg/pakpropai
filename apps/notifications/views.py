from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification


def _serialize(n: Notification) -> dict:
    return {
        'id':         str(n.id),
        'title':      n.title,
        'message':    n.message,
        'channel':    n.channel,
        'status':     n.status,
        'is_read':    n.is_read,
        'created_at': n.created_at.isoformat(),
        'sent_at':    n.sent_at.isoformat() if n.sent_at else None,
    }


class NotificationListView(APIView):
    """GET /notifications/ — authenticated user's own notification inbox."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (
            Notification.objects
            .filter(user=request.user)
            .order_by('-created_at')
        )

        unread_only = request.query_params.get('unread') == 'true'
        if unread_only:
            qs = qs.filter(is_read=False)

        limit = min(int(request.query_params.get('limit', 50)), 200)
        items = qs[:limit]

        return Response({
            'count':        qs.count(),
            'unread_count': Notification.objects.filter(user=request.user, is_read=False).count(),
            'results':      [_serialize(n) for n in items],
        })


class MarkReadView(APIView):
    """POST /notifications/mark-read/ — mark notifications as read.
    Body: { "ids": ["uuid1", "uuid2"] } or {} to mark all as read.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ids = request.data.get('ids')
        qs = Notification.objects.filter(user=request.user, is_read=False)
        if ids:
            qs = qs.filter(id__in=ids)
        updated = qs.update(is_read=True)
        return Response({'marked_read': updated})
