from django.urls import path
from rest_framework.routers import DefaultRouter, SimpleRouter
from .views import LeadViewSet, AppointmentViewSet
from .task_views import LeadTaskViewSet
from .admin_views import DuplicateLeadView, BulkAssignLeadsView, MergeLeadsView
from . import pipeline_views as PV
from . import assignment_views as AV

router = DefaultRouter()
router.register('', LeadViewSet, basename='leads')

# SimpleRouter (not DefaultRouter) avoids generating an api-root at ^$ that
# would intercept the leads-list URL when appt_router.urls is placed first.
appt_router = SimpleRouter()
appt_router.register('appointments', AppointmentViewSet, basename='appointments')
appt_router.register('tasks', LeadTaskViewSet, basename='lead-tasks')   # T-3020

urlpatterns = [
    # Fixed paths and appt_router MUST come before router.urls so they are not
    # swallowed by the leads router's ^(?P<pk>[^/.]+)/$ detail pattern.
    path('duplicates/',  DuplicateLeadView.as_view(),    name='lead-duplicates'),
    path('bulk-assign/', BulkAssignLeadsView.as_view(),  name='leads-bulk-assign'),
    path('merge/',       MergeLeadsView.as_view(),       name='leads-merge'),
    # T-3030 pipeline stages
    path('pipeline/stages/', PV.StageListView.as_view(), name='pipeline-stages'),
    path('pipeline/stages/reorder/', PV.StageReorderView.as_view(), name='pipeline-stages-reorder'),
    path('pipeline/stages/<uuid:pk>/', PV.StageDetailView.as_view(), name='pipeline-stage'),
    path('pipeline/stages/<uuid:pk>/archive/', PV.StageArchiveView.as_view(), name='pipeline-stage-archive'),
    path('pipeline/board/', PV.BoardView.as_view(), name='pipeline-board'),
    # T-3040 team and assignment rules
    path('assignment/rules/', AV.RulesView.as_view(), name='assignment-rules'),
    path('assignment/team/', AV.TeamView.as_view(), name='assignment-team'),
    path('assignment/team/<int:agent_id>/', AV.TeamMemberView.as_view(), name='assignment-team-member'),
] + appt_router.urls + router.urls
