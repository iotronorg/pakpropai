"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
# config/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from apps.core.urls import api_urlpatterns as core_api_urlpatterns
from apps.core.views import metrics_view
from apps.organizations.theme_views import ThemeConfigView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('metrics/', metrics_view, name='prometheus-metrics'),
    path('public/', include('apps.campaigns.public_urls')),
    path('', include('apps.core.urls')),
    path('api/v1/', include([
        path('config/',        include('apps.config.urls')),
        path('auth/',          include('apps.users.urls')),
        path('organizations/', include('apps.organizations.urls')),
        path('leads/',        include('apps.leads.urls')),
        path('agents/',       include('apps.agents.urls')),
        path('deals/',        include('apps.escrow.urls')),
        path('payments/',     include('apps.payments.urls')),
        path('whatsapp/',     include('apps.whatsapp.urls')),
        path('properties/',   include('apps.properties.urls')),
        path('verification/', include('apps.verification.urls')),
        path('audit/',         include('apps.audit.urls')),
        path('reports/',       include('apps.reports.urls')),
        path('notifications/', include('apps.notifications.urls')),
        path('campaigns/',    include('apps.campaigns.urls')),
        path('billing/',      include('apps.billing.urls')),
        path('compliance/',    include('apps.compliance.urls')),
        path('observability/', include('apps.observability.urls')),
        path('ai/', include('apps.ai.urls')),
        path('provisioning/', include('apps.whatsapp.provisioning_urls')),
        path('security/',     include('apps.security.urls')),
        path('inventory/',    include('apps.inventory.urls')),
        path('sla/',          include('apps.resilience.urls')),
        path('external/',      include('apps.organizations.external_urls')),
        path('theme/',         ThemeConfigView.as_view(),  name='theme-config'),
        *core_api_urlpatterns,
    ])),
]

if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [path('__debug__/', include(debug_toolbar.urls))] + urlpatterns
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)