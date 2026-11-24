from django.urls import path

from .views import EnquiryListView

urlpatterns = [path('', EnquiryListView.as_view(), name='enquiry-list')]
