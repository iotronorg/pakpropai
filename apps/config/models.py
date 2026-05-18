from django.db import models


class SystemConfig(models.Model):

    SENSITIVE_KEYS = {
        'wa_access_token', 'wa_app_secret', 'gemini_api_key',
        'safepay_merchant_key', 'safepay_secret_key',
        'bsecure_client_id', 'bsecure_client_secret',
    }

    REQUIRED_KEYS = {
        'wa_access_token', 'wa_phone_number_id', 'wa_verify_token', 'gemini_api_key',
    }

    # Maps config keys → Django settings attribute names (env fallback)
    ENV_KEY_MAP = {
        'wa_access_token':       'WA_ACCESS_TOKEN',
        'wa_phone_number_id':    'WA_PHONE_NUMBER_ID',
        'wa_app_secret':         'WA_APP_SECRET',
        'wa_verify_token':       'WA_VERIFY_TOKEN',
        'wa_otp_template_name':  'WA_OTP_TEMPLATE_NAME',
        'gemini_api_key':        'GEMINI_API_KEY',
        'gemini_model':          'GEMINI_MODEL',
        'ai_backend':            'AI_BACKEND',
        'base_url':              'BASE_URL',
        'safepay_merchant_key':  'SAFEPAY_MERCHANT_KEY',
        'safepay_secret_key':    'SAFEPAY_SECRET_KEY',
        'safepay_environment':   'SAFEPAY_ENVIRONMENT',
        'bsecure_client_id':     'BSECURE_CLIENT_ID',
        'bsecure_client_secret': 'BSECURE_CLIENT_SECRET',
        'bsecure_environment':   'BSECURE_ENVIRONMENT',
    }

    DEFAULTS = {
        'wa_access_token':            '',
        'wa_phone_number_id':         '',
        'wa_verify_token':            '',
        'wa_app_secret':              '',
        'wa_otp_template_name':       'otp_verification',
        'gemini_api_key':             '',
        'gemini_model':               'gemini-2.5-flash-lite',
        'ai_backend':                 'gemini',
        'base_url':                   'http://localhost:8000',
        'active_payment_gateway':     'manual',
        'jazzcash_number':            '',
        'easypaisa_number':           '',
        'bank_account_number':        '',
        'bank_account_name':          '',
        'safepay_merchant_key':       '',
        'safepay_secret_key':         '',
        'safepay_environment':        'sandbox',
        'bsecure_client_id':          '',
        'bsecure_client_secret':      '',
        'bsecure_environment':        'sandbox',
        'feature_property_search':    'true',
        'feature_property_listing':   'true',
        'feature_tax_advice':         'true',
        'feature_loan_eligibility':   'true',
        'feature_scam_check':         'true',
        'feature_document_verification': 'true',
        'feature_property_audit':     'true',
        'feature_talk_to_agent':      'true',
        'feature_deal_lock':          'true',
        'feature_voice_messages':     'true',
        'scraper_search_enabled':     'true',
    }

    key        = models.CharField(max_length=100, unique=True, db_index=True)
    value      = models.TextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        'users.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='config_changes',
    )

    class Meta:
        app_label = 'sysconfig'
        db_table  = 'system_config'
        verbose_name = 'System Config'

    def __str__(self):
        return self.key
