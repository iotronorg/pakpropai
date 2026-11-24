from django.urls import path
from apps.ai.views import TokenUsageStatsView, TokenBudgetStatusView
from apps.ai.cost_views import AICostReportView

urlpatterns = [
    path('token-usage/', TokenUsageStatsView.as_view(), name='ai-token-usage'),
    path('token-budget/', TokenBudgetStatusView.as_view(), name='ai-token-budget'),
    path('costs/', AICostReportView.as_view(), name='ai-costs'),
]
