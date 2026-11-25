from django.urls import path
from apps.ai.views import TokenUsageStatsView, TokenBudgetStatusView
from apps.ai.cost_views import AICostReportView
from apps.ai.kill_switch_views import KillSwitchListView, KillSwitchReleaseView

urlpatterns = [
    path('token-usage/', TokenUsageStatsView.as_view(), name='ai-token-usage'),
    path('token-budget/', TokenBudgetStatusView.as_view(), name='ai-token-budget'),
    path('costs/', AICostReportView.as_view(), name='ai-costs'),
    path('kill-switches/', KillSwitchListView.as_view(), name='ai-kill-switches'),
    path('kill-switches/<uuid:pk>/release/', KillSwitchReleaseView.as_view(), name='ai-kill-switch-release'),
]
