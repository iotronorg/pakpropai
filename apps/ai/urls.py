from django.urls import path
from apps.ai.views import TokenUsageStatsView, TokenBudgetStatusView
from apps.ai.cost_views import AICostReportView
from apps.ai.kill_switch_views import KillSwitchListView, KillSwitchReleaseView
from apps.ai import ops_views as O

urlpatterns = [
    path('token-usage/', TokenUsageStatsView.as_view(), name='ai-token-usage'),
    path('token-budget/', TokenBudgetStatusView.as_view(), name='ai-token-budget'),
    path('costs/', AICostReportView.as_view(), name='ai-costs'),
    path('kill-switches/', KillSwitchListView.as_view(), name='ai-kill-switches'),
    path('kill-switches/<uuid:pk>/release/', KillSwitchReleaseView.as_view(), name='ai-kill-switch-release'),
    # AI ops console (T-3090)
    path('ops/overview/', O.OverviewView.as_view(), name='ai-ops-overview'),
    path('ops/prompts/', O.PromptListView.as_view(), name='ai-ops-prompts'),
    path('ops/prompts/<str:name>/', O.PromptDetailView.as_view(), name='ai-ops-prompt'),
    path('ops/models/', O.ModelListView.as_view(), name='ai-ops-models'),
    path('ops/model-prices/', O.ModelPriceView.as_view(), name='ai-ops-model-prices'),
    path('ops/tools/', O.ToolListView.as_view(), name='ai-ops-tools'),
    path('ops/evals/', O.EvalRunListView.as_view(), name='ai-ops-evals'),
    path('ops/evals/<uuid:pk>/', O.EvalRunDetailView.as_view(), name='ai-ops-eval'),
    path('ops/traces/', O.TraceListView.as_view(), name='ai-ops-traces'),
    path('ops/traces/<uuid:pk>/', O.TraceDetailView.as_view(), name='ai-ops-trace'),
]
